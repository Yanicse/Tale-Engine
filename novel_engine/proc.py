"""Run a child process with a timeout and stop its whole process tree, on Windows and POSIX."""
import os
import signal
import subprocess


def popen_options():
    """Keyword arguments that let the whole tree be stopped later."""
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}


def kill_tree(process):
    """Stop the process and its children; True when the tree is known to be stopped."""
    stopped = os.name != 'nt'
    try:
        if os.name == 'nt':
            killed = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
            stopped = killed.returncode == 0
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        stopped = False
    try:
        process.kill()
    except OSError:
        pass
    return stopped


def run(command, data, timeout, env=None, cwd=None):
    """(returncode, stdout, stderr); raises subprocess.TimeoutExpired after stopping the tree."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, cwd=cwd, **popen_options())
    try:
        out, err = process.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        process.communicate()
        raise
    return process.returncode, out, err
