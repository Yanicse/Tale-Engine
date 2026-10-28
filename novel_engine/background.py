"""Bounded and persistent queue; the player's message interrupts the work."""
import json
import threading
import time
import uuid

from .adapters import DisabledAdapter, ModelUnavailable
from .campaign import conditions_hold
from .contracts import check, check_json


class Background:
    def __init__(self, campaign, handler, context_builder=None, adapter=None):
        self.campaign = campaign
        self.handler = handler
        self.context_builder = context_builder
        self.adapter = adapter or DisabledAdapter()
        self.cancel = threading.Event()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.gate = threading.RLock()
        self.thread = None
        self.cycle = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name='background-evolutions', daemon=True)
        self.thread.start()

    def reading(self, max_tasks=4, max_calls=1):
        if type(max_tasks) is not int or type(max_calls) is not int or max_tasks < 0 or max_calls < 0:
            raise ValueError('Positive or zero integer budgets expected')
        with self.gate:
            self.interrupt()
            self.cancel = threading.Event()
            self.cycle = uuid.uuid4().hex
            with self.campaign.transaction() as con:
                con.execute('INSERT INTO cycles (cycle_id,active,max_tasks,max_calls) VALUES (?,1,?,?)',
                            (self.cycle,max_tasks,max_calls))
            return self.cycle

    def enqueue(self, task):
        check('task', task)
        with self.gate, self.campaign.transaction() as con:
            cycle = con.execute('SELECT * FROM cycles WHERE cycle_id=?', (self.cycle,)).fetchone()
            if not cycle or not cycle['active']:
                raise ValueError('No active reading period')
            existing = con.execute('SELECT spec FROM tasks WHERE task_id=?', (task['task_id'],)).fetchone()
            if existing:
                if json.loads(existing[0]) != task:
                    raise ValueError('Task identifier reused')
                return
            count = con.execute('SELECT COUNT(*) FROM tasks WHERE cycle_id=?', (self.cycle,)).fetchone()[0]
            if count >= cycle['max_tasks']:
                raise ValueError('Task budget reached')
            state = self.campaign._state(con)
            if task['campaign_id'] != state['campaign_id'] or task['expected_state_version'] != state['state_version']:
                raise ValueError('Task created on an obsolete state')
            con.execute('INSERT INTO tasks(task_id,cycle_id,spec,status) VALUES (?,?,?,?)',
                        (task['task_id'],self.cycle,check_json(task),'pending'))
        self.wake.set()

    def interrupt(self):
        with self.gate:
            self.cancel.set()
            if self.cycle:
                with self.campaign.transaction() as con:
                    con.execute('UPDATE cycles SET active=0 WHERE cycle_id=?', (self.cycle,))
                    con.execute(("UPDATE tasks SET status='cancelled',reason='Player message takes priority' WHERE "
                                 "cycle_id=? AND status IN ('pending','in_progress')"), (self.cycle,))
            self.wake.set()

    def submit_player(self, proposal):
        with self.gate:
            self.interrupt()
            return self.campaign.apply(proposal)

    def close(self):
        self.interrupt()
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def status(self):
        with self.campaign.transaction() as con:
            return [dict(row) for row in con.execute('SELECT task_id,status,reason FROM tasks ORDER BY rowid')]

    def _loop(self):
        while not self.stop_event.is_set():
            self.wake.wait()
            self.wake.clear()
            while not self.stop_event.is_set() and self._one():
                pass

    def _one(self):
        with self.gate, self.campaign.transaction() as con:
            row = con.execute("SELECT t.* FROM tasks t JOIN cycles c ON c.cycle_id=t.cycle_id WHERE c.active=1 AND t.status='pending' ORDER BY t.rowid LIMIT 1").fetchone()
            if not row:
                return False
            task = json.loads(row['spec'])
            state = self.campaign._state(con)
            cancel = self.cancel
            if task['expected_state_version'] != state['state_version'] or not conditions_hold(state,task['conditions']):
                con.execute(("UPDATE tasks SET status='rejected',reason='Incompatible version or conditions' WHERE "
                             "task_id=?"), (task['task_id'],))
                return True
            if task['handler'] == 'model':
                cycle = con.execute('SELECT * FROM cycles WHERE cycle_id=?', (row['cycle_id'],)).fetchone()
                if task['budget']['max_model_calls'] < 1 or cycle['calls'] >= cycle['max_calls']:
                    con.execute("UPDATE tasks SET status='rejected',reason='Model budget reached' WHERE task_id=?", (task['task_id'],))
                    return True
                con.execute('UPDATE cycles SET calls=calls+1 WHERE cycle_id=?', (row['cycle_id'],))
            con.execute("UPDATE tasks SET status='in_progress' WHERE task_id=?", (task['task_id'],))
        started = time.monotonic()
        try:
            if cancel.is_set():
                return True
            if task['handler'] == 'model':
                if not self.context_builder:
                    raise ValueError('Context builder missing')
                context = self.context_builder(task)
                if cancel.is_set():
                    return True
                proposal = self.adapter.proposed(context,task)
            else:
                proposal = self.handler(task,state,cancel)
            if cancel.is_set():
                return True
            if time.monotonic()-started > task['budget']['max_seconds']:
                raise ValueError('Computation timeout exceeded')
            check('proposal',proposal)
            if proposal['narration'] and 'player' not in task['allowed_audience']:
                raise ValueError('Public narration forbidden for this secret task')
            if proposal['request_id'] != task['task_id'] or proposal['expected_state_version'] != task['expected_state_version']:
                raise ValueError('Response not attached to the task')
            for event in proposal['events']:
                if event['world_minute'] < task['not_before_world_minute']:
                    raise ValueError('Task deadline not met')
                if not set(event['audience']) <= set(task['allowed_audience']):
                    raise ValueError('Audience not authorized')
                if any(change['path'] not in task['allowed_paths'] for change in event['changes']):
                    raise ValueError('Change outside the authorized scope')
        except Exception as exc:
            reason = str(exc) if isinstance(exc,ModelUnavailable) else 'Computation or validation failed; no change applied'
            with self.campaign.transaction() as con:
                con.execute("UPDATE tasks SET status='error',reason=? WHERE task_id=? AND status='in_progress'", (reason,task['task_id']))
            return True
        with self.gate, self.campaign.transaction() as con:
            row = con.execute('SELECT t.status,c.active FROM tasks t JOIN cycles c ON c.cycle_id=t.cycle_id WHERE task_id=?', (task['task_id'],)).fetchone()
            if cancel.is_set() or row['status'] != 'in_progress' or not row['active']:
                return True
            con.execute("UPDATE tasks SET status='prepared',proposal=? WHERE task_id=?", (check_json(proposal),task['task_id']))
            if task['kind'] == 'autonomous':
                result = self.campaign._apply(con,proposal,background=True)
                con.execute('UPDATE tasks SET status=?,reason=? WHERE task_id=?',
                            (result['status'],result['reason'],task['task_id']))
        return True

    def settle_prepared(self):
        """Recheck after the player's action; no additional generation."""
        with self.gate, self.campaign.transaction() as con:
            rows = con.execute("SELECT * FROM tasks WHERE status IN ('deferred','prepared')").fetchall()
            for row in rows:
                task = json.loads(row['spec'])
                state = self.campaign._state(con)
                if task['expected_state_version'] != state['state_version'] or not conditions_hold(state,task['conditions']):
                    con.execute("UPDATE tasks SET status='rejected',reason='Preparation became obsolete' WHERE task_id=?", (task['task_id'],))
                elif task['kind'] == 'autonomous':
                    result = self.campaign._apply(con,json.loads(row['proposal']),background=True)
                    con.execute('UPDATE tasks SET status=?,reason=? WHERE task_id=?', (result['status'],result['reason'],task['task_id']))
                # Conditional preparations require an explicit confirmation.
