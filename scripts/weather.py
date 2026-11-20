"""Free personal weather adapter. Forecast resolution is never relabelled radar nowcast."""
import copy, json, math, threading, time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CACHE={}; LOCK=threading.Lock(); TTL=600
ALLOWED_ENDPOINTS={('api.open-meteo.com','/v1/forecast'),('geocoding-api.open-meteo.com','/v1/search')}
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*a,**kw): raise ValueError('天气服务返回了异常跳转')

def fetch(host,path,params):
    if (host,path) not in ALLOWED_ENDPOINTS: raise ValueError('天气来源无效')
    url='https://'+host+path+'?'+urlencode(params)
    with build_opener(NoRedirect).open(Request(url,headers={'User-Agent':'Tiangong-Personal/30','Accept':'application/json'}),timeout=14) as response:
        raw=response.read(2_000_001)
        if len(raw)>2_000_000: raise ValueError('天气数据过大')
        return json.loads(raw)

def number(v,lo,hi):
    if isinstance(v,bool): raise ValueError('地点坐标无效')
    try: n=float(v)
    except (ValueError,TypeError): raise ValueError('地点坐标无效') from None
    if not math.isfinite(n) or not lo<=n<=hi: raise ValueError('地点坐标无效')
    return round(n,3)

def metric(v,lo=-1e6,hi=1e6):
    return v if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and lo<=v<=hi else None

def times(value,limit,required=False):
    if value in (None,[]) and not required:return []
    if not isinstance(value,list) or not value or len(value)>limit:raise ValueError('天气时段无效')
    if any(metric(t,0,253402300799) is None for t in value):raise ValueError('天气时间无效')
    if any(a>=b for a,b in zip(value,value[1:])):raise ValueError('天气时段顺序异常')
    return value

def normalize(raw):
    """Validate the time axis; absent or invalid metrics stay unknown, not zero."""
    if not isinstance(raw,dict) or raw.get('error'):raise ValueError('天气服务暂未返回有效预报')
    current=raw.get('current');hourly=raw.get('hourly');daily=raw.get('daily')
    if not isinstance(current,dict) or metric(current.get('time'),0,253402300799) is None or not isinstance(hourly,dict):
        raise ValueError('天气服务暂未返回有效预报')
    tz=raw.get('timezone')
    if not isinstance(tz,str) or len(tz)>100:raise ValueError('天气时区无效')
    try:ZoneInfo(tz)
    except (ZoneInfoNotFoundError,ValueError):raise ValueError('天气时区无效') from None
    hourly_times=times(hourly.get('time'),400,True)
    daily=daily if isinstance(daily,dict) else {}
    daily_times=times(daily.get('time'),16)
    fields={'temperature_2m':(-150,100),'relative_humidity_2m':(0,100),'apparent_temperature':(-150,100),
            'precipitation':(0,10000),'precipitation_probability':(0,100),'weather_code':(0,99),
            'wind_speed_10m':(0,1000),'is_day':(0,1),'temperature_2m_max':(-150,100),
            'temperature_2m_min':(-150,100),'precipitation_probability_max':(0,100),
            'sunrise':(0,253402300799),'sunset':(0,253402300799)}
    current_fields=('temperature_2m','relative_humidity_2m','apparent_temperature','precipitation','weather_code','wind_speed_10m','is_day')
    clean_current={k:metric(current.get(k),*fields[k]) for k in current_fields}
    clean_current.update(time=current['time'],interval=metric(current.get('interval'),1,86400))
    def series(block,axis,names):
        out={'time':axis}
        for name in names:
            values=block.get(name)
            out[name]=[metric(values[i],*fields[name]) if isinstance(values,list) and i<len(values) else None for i in range(len(axis))]
        return out
    return {'current':clean_current,'hourly':series(hourly,hourly_times,('temperature_2m','precipitation_probability','precipitation','weather_code')),
            'daily':series(daily,daily_times,('weather_code','temperature_2m_max','temperature_2m_min','precipitation_probability_max','sunrise','sunset')),
            'current_units':{'temperature_2m':'°C','wind_speed_10m':'km/h','precipitation':'mm'},
            'hourly_units':{'temperature_2m':'°C','precipitation':'mm'},'daily_units':{'temperature_2m_min':'°C','temperature_2m_max':'°C'},
            'timezone':tz,'utc_offset_seconds':metric(raw.get('utc_offset_seconds'),-86400,86400)}

def handle(data):
    if not isinstance(data,dict): raise ValueError('天气请求无效')
    if data.get('action')=='search':
        term=data.get('name','')
        if not isinstance(term,str) or not 2<=len(term.strip())<=60: raise ValueError('请输入 2 至 60 字的城市名称')
        try:
            result=fetch('geocoding-api.open-meteo.com','/v1/search',{'name':term.strip(),'count':8,'language':'zh','format':'json'})
            if not isinstance(result,dict) or result.get('error'):raise ValueError()
            rows=result.get('results') or []
            if not isinstance(rows,list):raise ValueError()
            places=[]
            for r in rows[:8]:
                if not isinstance(r,dict) or not isinstance(r.get('name'),str):continue
                try:lat=number(r.get('latitude'),-90,90);lon=number(r.get('longitude'),-180,180)
                except ValueError:continue
                places.append(dict(name=r['name'][:100],latitude=lat,longitude=lon,
                                   **{k:r.get(k)[:100] if isinstance(r.get(k),str) else '' for k in ('country','admin1','timezone')}))
            return {'places':places}
        except Exception:raise ValueError('城市搜索暂时不可用，请稍后重试或选择常用城市。') from None
    if data.get('action','forecast')!='forecast': raise ValueError('天气操作无效')
    lat=number(data.get('latitude'),-90,90); lon=number(data.get('longitude'),-180,180); key=(lat,lon)
    now=time.time()
    with LOCK: old=CACHE.get(key)
    if old and 0<=now-old['fetched_at']<TTL:return copy.deepcopy(dict(old,stale=False,server_time=now))
    params={'latitude':lat,'longitude':lon,'timezone':'auto','timeformat':'unixtime','forecast_days':7,
      'temperature_unit':'celsius','wind_speed_unit':'kmh','precipitation_unit':'mm',
      'current':'temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,is_day',
      'hourly':'temperature_2m,precipitation_probability,precipitation,weather_code',
      'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset'}
    try:
        raw=fetch('api.open-meteo.com','/v1/forecast',params)
        result=normalize(raw)
        fetched=time.time()
        result.update(fetched_at=fetched,server_time=fetched,source='Open-Meteo',resolution='hourly',stale=False,latitude=lat,longitude=lon)
        with LOCK:
            if len(CACHE)>=64: CACHE.pop(next(iter(CACHE)))
            CACHE[key]=result
        return copy.deepcopy(result)
    except Exception:
        if old and 0<=now-old['fetched_at']<6*3600:return copy.deepcopy(dict(old,stale=True,server_time=time.time()))
        raise ValueError('天气服务暂时连接不上，请稍后刷新；没有使用示例天气替代。') from None
