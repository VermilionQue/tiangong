"""Read Shanjun's daily text without making model calls on page visits."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import tenant_storage
FILE=tenant_storage.path('daily-quotes.json')
FALLBACK=['把今天的一小步，走得踏实一点。','先完成一件小事，再向前走一点。','给自己一点耐心，积累正在发生。','不必一次做完，今天也可以是开始。','把注意力放回眼前，认真做好这一刻。','允许步子小一点，但记得继续走。','休息好，再去做你在意的事。']
def today():
    now=datetime.now(ZoneInfo('Asia/Shanghai'));date=now.date().isoformat()
    try:
        q=json.loads(FILE.read_text()).get(date,{})
        if isinstance(q,dict) and isinstance(q.get('text'),str) and 0<len(q['text'].strip())<=160:
            return {'date':date,'text':q['text'].strip(),'source':q.get('source','山君')}
    except (OSError,ValueError,AttributeError):pass
    return {'date':date,'text':FALLBACK[now.date().toordinal()%len(FALLBACK)],'source':'天工录备用句'}
