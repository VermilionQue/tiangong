"""Forecast integrity, honest cache age and city-selection race regression tests."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import weather


def forecast():
    return {'current': {'time': 10000, 'interval': 900, 'temperature_2m': 20, 'weather_code': 0},
            'hourly': {'time': [10800 + i * 3600 for i in range(24)], 'temperature_2m': [20] * 24,
                       'precipitation': [0] * 24, 'precipitation_probability': [5] * 24, 'weather_code': [0] * 24},
            'daily': {'time': [86400 * i for i in range(7)], 'temperature_2m_max': [23] * 7,
                      'temperature_2m_min': [17] * 7, 'weather_code': [0] * 7},
            'timezone': 'Asia/Shanghai', 'utc_offset_seconds': 28800}


class WeatherTest(unittest.TestCase):
    def setUp(self):
        weather.CACHE.clear()
        self.coords = {'latitude': 30.274123, 'longitude': 120.155321}

    def tearDown(self):
        weather.CACHE.clear()

    def test_cache_ttl_and_caller_cannot_mutate_cached_forecast(self):
        with patch.object(weather, 'fetch', return_value=forecast()) as fetch, patch.object(weather.time, 'time', return_value=10000):
            first = weather.handle(self.coords)
            self.assertEqual((first['latitude'], first['longitude']), (30.274, 120.155))
            self.assertEqual(first['resolution'], 'hourly')
            first['hourly']['temperature_2m'][0] = -99
        with patch.object(weather, 'fetch') as fetch, patch.object(weather.time, 'time', return_value=10599):
            second = weather.handle(self.coords)
            fetch.assert_not_called()
            self.assertEqual(second['hourly']['temperature_2m'][0], 20)
            self.assertEqual(second['server_time'], 10599)
        with patch.object(weather, 'fetch', return_value=forecast()) as fetch, patch.object(weather.time, 'time', return_value=10600):
            weather.handle(self.coords)
            fetch.assert_called_once()

    def test_failure_uses_marked_cache_only_for_less_than_six_hours(self):
        with patch.object(weather, 'fetch', return_value=forecast()), patch.object(weather.time, 'time', return_value=10000):
            weather.handle(self.coords)
        with patch.object(weather, 'fetch', side_effect=TimeoutError()), patch.object(weather.time, 'time', return_value=12000):
            cached = weather.handle(self.coords)
            self.assertTrue(cached['stale'])
            self.assertEqual(cached['fetched_at'], 10000)
            self.assertEqual(cached['server_time'], 12000)
        with patch.object(weather, 'fetch', side_effect=TimeoutError()), patch.object(weather.time, 'time', return_value=31600):
            with self.assertRaisesRegex(ValueError, '没有使用示例'):
                weather.handle(self.coords)

    def test_invalid_forecast_does_not_poison_cache(self):
        with patch.object(weather, 'fetch', return_value=forecast()), patch.object(weather.time, 'time', return_value=10000):
            weather.handle(self.coords)
        for raw in (None, [], {'error': True}, {'current': None}, {**forecast(), 'timezone': 'not/a/timezone'},
                    {**forecast(), 'hourly': {'time': [10800, 10800]}}, {**forecast(), 'hourly': {'time': [None]}}):
            with self.subTest(raw=raw), patch.object(weather, 'fetch', return_value=raw), patch.object(weather.time, 'time', return_value=12000):
                result = weather.handle(self.coords)
                self.assertTrue(result['stale'])
                self.assertEqual(weather.CACHE[(30.274, 120.155)]['fetched_at'], 10000)

    def test_null_and_nonfinite_metrics_stay_unknown(self):
        raw = forecast()
        raw['current']['temperature_2m'] = None
        raw['current']['wind_speed_10m'] = float('nan')
        raw['hourly']['precipitation'] = [None, float('inf'), -1, '2', True, 0.5]
        raw['daily'] = None
        result = weather.normalize(raw)
        self.assertIsNone(result['current']['temperature_2m'])
        self.assertIsNone(result['current']['wind_speed_10m'])
        self.assertEqual(result['hourly']['precipitation'][:6], [None, None, None, None, None, 0.5])
        self.assertEqual(result['daily']['time'], [])
        self.assertEqual(len(result['hourly']['precipitation']), 24)
        json.dumps(result, allow_nan=False)

    def test_bad_coordinates_rejected_before_network(self):
        for data in (None, {}, {'latitude': True, 'longitude': 10}, {'latitude': 'nan', 'longitude': 20},
                     {'latitude': 91, 'longitude': 20}, {'latitude': 30, 'longitude': 181}):
            with self.subTest(data=data), patch.object(weather, 'fetch') as fetch, self.assertRaises(ValueError):
                weather.handle(data)
            fetch.assert_not_called()

    def test_search_null_and_invalid_results(self):
        with patch.object(weather, 'fetch', return_value={'results': None}):
            self.assertEqual(weather.handle({'action': 'search', 'name': '杭州'}), {'places': []})
        rows = [None, {'name': 'Bad', 'latitude': 200, 'longitude': 0},
                {'name': '杭州', 'latitude': 30.274123, 'longitude': 120.155, 'country': None}]
        with patch.object(weather, 'fetch', return_value={'results': rows}):
            places = weather.handle({'action': 'search', 'name': '杭州'})['places']
            self.assertEqual(len(places), 1)
            self.assertEqual(places[0]['latitude'], 30.274)
            self.assertEqual(places[0]['country'], '')
        with patch.object(weather, 'fetch', return_value=None), self.assertRaisesRegex(ValueError, '搜索暂时不可用'):
            weather.handle({'action': 'search', 'name': '杭州'})

    def test_forecast_uses_explicit_units_and_unix_time(self):
        with patch.object(weather, 'fetch', return_value=forecast()) as fetch:
            weather.handle(self.coords)
            host, path, params = fetch.call_args.args
            self.assertEqual((host, path), ('api.open-meteo.com', '/v1/forecast'))
            self.assertEqual((params['timezone'], params['timeformat'], params['wind_speed_unit']), ('auto', 'unixtime', 'kmh'))
            self.assertNotIn('minutely_15', params)

    def test_fixed_hosts_response_limit_and_redirect_rejection(self):
        with patch.object(weather, 'build_opener') as opener:
            with self.assertRaisesRegex(ValueError, '来源无效'):
                weather.fetch('127.0.0.1', '/v1/forecast', {})
            opener.assert_not_called()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, maximum): return b' ' * maximum
        with patch.object(weather, 'build_opener') as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaisesRegex(ValueError, '过大'):
                weather.fetch('api.open-meteo.com', '/v1/forecast', {})
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 14)
        with self.assertRaisesRegex(ValueError, '跳转'):
            weather.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/')

    def test_cache_capacity_is_bounded(self):
        with patch.object(weather, 'fetch', return_value=forecast()), patch.object(weather.time, 'time', return_value=10000):
            for longitude in range(70):
                weather.handle({'latitude': 30, 'longitude': longitude})
        self.assertEqual(len(weather.CACHE), 64)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the asynchronous UI regression')
    def test_frontend_city_search_and_location_races(self):
        source = Path(__file__).resolve().parents[1] / 'src/web/weather.js'
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map(), pending=[];let locate;
class Element {constructor(){this.textContent='';this.innerHTML='';this.value='';this.hidden=false;}replaceChildren(){this.innerHTML='';this.textContent='';}append(){}setAttribute(){}querySelectorAll(){return [];}}
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const context={document:{getElementById:get,createElement:()=>new Element()},window:{},navigator:{geolocation:{getCurrentPosition:ok=>locate=ok}},localStorage:{getItem:()=>null,setItem:()=>{}},AbortController,Intl,Date,JSON,Number,Math,Error,TypeError,setTimeout:()=>0,clearTimeout:()=>{},fetch:()=>new Promise(resolve=>pending.push(resolve))};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const response=value=>({ok:true,json:async()=>value});
const data=temp=>({timezone:'Atlantic/Reykjavik',server_time:1700000000,fetched_at:1700000000,current:{time:1700000000,temperature_2m:temp,weather_code:null},hourly:{time:Array.from({length:24},(_,i)=>1700002800+i*3600),temperature_2m:Array(24).fill(null),precipitation:Array(24).fill(null)},daily:{time:[]}});
const choose=i=>get('weather-presets').onclick({target:{closest:()=>({dataset:{city:String(i)}})}});
(async()=>{
const first=context.window.loadWeather();choose(1);assert.equal(pending.length,2);
pending[1](response(data(20)));await tick();await tick();assert.equal(get('weather-city').textContent,'上海');
pending[0](response(data(10)));await first;assert.equal(get('weather-city').textContent,'上海');
assert(get('weather-rain-summary').textContent.includes('暂缺'));assert(get('weather-updated').textContent.includes('Atlantic/Reykjavik'));
assert.equal(get('weather-description').textContent,'暂无天气描述');
get('weather-locate').onclick();choose(2);const count=pending.length;locate({coords:{latitude:31,longitude:121}});assert.equal(pending.length,count);
pending[count-1](response(data(null)));await tick();await tick();assert.equal(get('weather-city').textContent,'绍兴');assert.equal(get('weather-temperature').textContent,'暂无数据');
get('weather-search').value='杭州';const search=get('weather-search-form').onsubmit({preventDefault(){}});const searchIndex=pending.length-1;choose(3);pending[searchIndex](response({places:[{name:'迟到的城市',latitude:1,longitude:1}]}));await search;assert.equal(get('weather-places').innerHTML,'');
pending[pending.length-1](response(data(18)));await tick();await tick();assert.equal(get('weather-city').textContent,'莆田');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', script, str(source)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
