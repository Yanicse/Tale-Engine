"""CLI errors: bounded and masked details, with no prompt and no narrative response."""
from datetime import datetime,timezone
import json
import re


MAX_DETAIL=4000


def safe_detail(value,limit=MAX_DETAIL):
    text=str(value).replace(chr(0x2014),'-')
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
    text=re.sub(r'(?im)\b((?:set[-_]?)?cookie\s*:\s*)[^\r\n]+',r'\1[mask]',text)
    text=re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s,;"\']+',r'\1 [mask]',text)
    text=re.sub(r'''(?ix)
        ((?:authorization|proxy.authorization|cookie|set.cookie|api[_-]?key|access[_-]?token|
          refresh[_-]?token|id[_-]?token|password|client[_-]?secret)["']?\s*[:=]\s*)
        (?:"[^"]*"|'[^']*'|[^\s,;}\]]+)
        ''',r'\1[mask]',text)
    text=re.sub(r'\bsk-[A-Za-z0-9_-]+|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',
                '[secret mask]',text)
    text=''.join(c for c in text if c in '\n\t' or ord(c)>=32)
    return text[:limit]


def decode_stream(raw):
    return raw.decode('utf-8',errors='replace') if isinstance(raw,bytes) else str(raw or '')


def skill_budget_warning(text):
    return 'exceeded skills context budget.' in text.casefold()


def error_message(value):
    if isinstance(value,str):return safe_detail(value)
    if isinstance(value,dict):
        # Do not serialize a request, its headers or its context.
        parts=[]
        for key in ('message','code','type','status','status_code','http_status','request_id','retry_after'):
            item=value.get(key)
            if isinstance(item,(str,int,float)) and not isinstance(item,bool):
                parts.append(safe_detail(item) if key=='message' else key+'='+safe_detail(item,160))
        return '; '.join(parts)
    return ''


def collect_failure(stdout,stderr,exit_code):
    events=[];messages=[];warnings=[];progress=[]
    for line in decode_stream(stdout).splitlines():
        try:event=json.loads(line)
        except (ValueError,TypeError):continue
        if not isinstance(event,dict):continue
        if event.get('type') in ('thread.started','turn.started','turn.completed'):
            progress.append(event['type'])
        if event.get('type') in ('turn.failed','error'):
            events.append(event['type'])
            message=error_message(event.get('error')) or error_message(event.get('message'))
            if message:messages.append(message)
        elif event.get('type')=='item.completed' and isinstance(event.get('item'),dict):
            item=event['item']
            if item.get('type')=='error':
                message=error_message(item.get('message')) or error_message(item.get('error'))
                if skill_budget_warning(message):warnings.append(message)
                elif exit_code:
                    events.append('item.error')
                    if message:messages.append(message)
    # Some CLI versions write a multiline JSON object to stderr.
    # Extract its cause before the warnings, without serializing the context.
    raw_stderr=decode_stream(stderr)
    decoder=json.JSONDecoder()
    for match in re.finditer(r'(?m)^\s*\{',raw_stderr):
        try: event,_=decoder.raw_decode(raw_stderr[match.start():].lstrip())
        except ValueError: continue
        if not isinstance(event,dict) or event.get('type') not in ('error','turn.failed'):
            continue
        events.append(event['type'])
        message=error_message(event.get('error')) or error_message(event.get('message'))
        if event.get('status') is not None:message+='; status='+str(event['status'])
        if message:messages.append(message)
    # Structured errors take priority; stderr may contain benign warnings.
    stderr_lines=[]
    for line in raw_stderr.splitlines():
        if skill_budget_warning(line):warnings.append(safe_detail(line))
        else:stderr_lines.append(line)
    tail='\n'.join(stderr_lines[-12:])
    tail=safe_detail(tail)
    detail='\n'.join(dict.fromkeys(messages))[:MAX_DETAIL]
    diagnostic=classify(detail or tail)
    return {'category':diagnostic['category'],'http_status':diagnostic['http_status'],
            'exit_code':exit_code,'events':events[:8],'detail':detail,
            'stderr_tail':tail,'warnings':list(dict.fromkeys(warnings))[:8],
            'progress_events':progress[-8:],
            'utc':datetime.now(timezone.utc).isoformat(timespec='seconds')}


def classify(text):
    lower=text.casefold()
    match=re.search(r'(?i)(?:http(?:/[0-9.]+)?\s*|status(?:[ _]+code)?\s*[:=]?\s*|api error\s*:\s*)([45]\d{2})\b',text)
    status=int(match[1]) if match else None
    if status is not None and status>=500:kind='service'
    elif status==429 or any(s in lower for s in ('rate limit','rate_limit','too many requests')):kind='rate_limit'
    elif any(s in lower for s in ('usage limit','usage_limit','quota exceeded','insufficient_quota')):kind='usage_limit'
    elif status is None and any(s in lower for s in ('service unavailable','service_unavailable',
            'server_error','internal server error','bad gateway','overloaded')):kind='service'
    elif status in (401,403) or any(s in lower for s in ('unauthorized','authentication','invalid token','token expired',
            'refresh token','not logged in','login required','re-authenticate')):kind='authentication'
    elif any(s in lower for s in ('permission denied','access is denied','access denied','access refuse','os error 5',
                                 'permissiondenied','operation not permitted')):kind='permissions'
    elif any(s in lower for s in ('invalid schema','invalid_json_schema','response_format','json schema')):kind='schema'
    elif any(s in lower for s in ('unexpected argument','unrecognized argument','unknown option','unknown flag')):kind='arguments'
    elif any(s in lower for s in ('stream disconnected','websocket','connection reset','connection refused',
            'failed to connect','error sending request','dns','tls','ssl','certificate','network')):kind='network'
    elif any(s in lower for s in ('timed out','timeout','deadline exceeded')):kind='timeout'
    else:kind='unknown'
    return {'category':kind,'http_status':status}


def failure_summary(error, provider='Codex'):
    titles={
        'service':'Service Codex temporarily unavailable',
        'network':'Codex network connection interrupted',
        'rate_limit':'Codex request rate limited',
        'usage_limit':'Codex usage limit reached',
        'authentication':'Codex authentication or access refused',
        'permissions':'Local access of the Codex CLI refused',
        'schema':'JSON schema refused by Codex',
        'arguments':'Codex CLI option refused',
        'timeout':'Codex generation timeout exceeded',
        'interrupted':'Codex generation interrupted',
        'invalid_response':'Codex CLI response invalid or missing',
        'unknown':'Codex CLI failure, cause not identified'}
    title=titles.get(error['category'],titles['unknown']).replace('Codex',provider)
    suffix=(' (HTTP '+str(error['http_status'])+')' if error.get('http_status') else
            ' (exit code '+str(error['exit_code'])+')' if error.get('exit_code') else '')
    message=title+suffix+'.'
    if error['category']=='timeout' and error.get('timeout_seconds'):
        message+=' Limite : '+str(error['timeout_seconds'])+' seconds.'
    detail=error.get('detail') or error.get('stderr_tail')
    # Old diagnostics may still contain this warning alone.
    if detail and skill_budget_warning(detail) and error['category']=='timeout':
        detail='\n'.join(line for line in detail.splitlines() if not skill_budget_warning(line))
    if detail:message+=' Detail: '+safe_detail(detail,900)
    elif error['category']=='timeout':
        message+=' No precise cause received from the service before the stop.'
    if error['category']=='authentication':
        message+=(' Check codex login status, then codex login if needed.' if provider=='Codex'
                  else ' Check claude auth status, then claude auth login if needed.')
    message+=' Input kept; no automatic retry.'
    return message
