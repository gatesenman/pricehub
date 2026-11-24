"""Price and event-time validation shared by manual and collector writes."""
import datetime as dt
import math

def amount(value, field='价格', minimum=0):
    if isinstance(value,bool): raise ValueError(field+'必须是数字')
    try: value=float(value)
    except (ValueError,TypeError,OverflowError): raise ValueError(field+'必须是数字')
    if not math.isfinite(value) or value < minimum: raise ValueError(field+'必须是有限非负数')
    return value

def timestamp(value):
    """Naive input keeps historical UTC contract; explicit offsets normalize to UTC."""
    if value in (None,''): return None
    if not isinstance(value,str): raise ValueError('时间必须为 ISO 8601')
    try:
        parsed=dt.datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError,OverflowError): raise ValueError('时间必须为 ISO 8601')

def observations(prices):
    """Ignore legacy malformed/nonfinite rows; stable event-time ordering for forecasting."""
    out=[]
    for i,row in enumerate(prices):
        try:
            value=amount(row.get('price') if isinstance(row,dict) else row)
            ts=timestamp(row.get('ts')) if isinstance(row,dict) else None
        except (ValueError,TypeError): continue
        out.append((ts,i,value))
    if out and all(ts is not None for ts,_,_ in out): out.sort(key=lambda r:(r[0],r[1]))
    return out
