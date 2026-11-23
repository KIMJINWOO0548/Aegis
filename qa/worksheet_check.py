"""Synthetic two-module check using the real collector, Gateway and built dashboard."""
from pathlib import Path
import sys, types, os, json, asyncio, socket, subprocess, secrets, hashlib, time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT.parent
WORKSPACE=WORK.parent
OUT=Path(os.environ.get('AEGIS_WORKSHEET_QA_OUTPUT', str(WORKSPACE/'outputs/desktop-redesign/aegis/actual'))).resolve()
SCRATCH=Path(os.environ.get('AEGIS_WORKSHEET_QA_STATE', str(ROOT/'runtime/worksheet-qa'))).resolve()
OUT.mkdir(parents=True,exist_ok=True)
SCRATCH.mkdir(parents=True,exist_ok=True)
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(WORK/'insure/.venv/Lib/site-packages'),str(WORK/'aegis-restart-qa')]
import sitecustomize  # Existing process-only loopback recovery for Windows EFAULT.
import httpx
from playwright.sync_api import sync_playwright,expect
from aegis.collector import load_sources
from aegis.processes import ProcessCollector
from scripts.benchmark_emulators import _ports,_stop,wait_until
# Reuse the proven synthetic SQLite seeder without its import-time scratch writes.
source=(WORKSPACE/'outputs/aegis/check_restart_fresh.py').read_text(encoding='utf-8')
source=source[:source.index('def verify_gateway(')]
start=source.index('OUT = '); end=source.index('import httpx',start)
source=source[:start]+source[end:]
fixture=types.ModuleType('worksheet_fixture')
fixture.__dict__.update(OUT=OUT,WORK=WORK,ROOT=ROOT,HARNESS=WORK/'aegis-restart-qa',SCRATCH=SCRATCH)
exec(compile(source,'reused_synthetic_fixture','exec'),fixture.__dict__)

def main():
 native='--native' in sys.argv or '--preview' in sys.argv
 preview='--preview' in sys.argv
 api_port,gateway_port,debug=_ports(3)
 env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(WORK/'aegis-restart-qa'),str(ROOT/'src'),str(Path(sys.prefix)/'Lib/site-packages')]),PYTHONUTF8='1',AEGIS_API_TOKEN=secrets.token_hex(32),AEGIS_EMULATOR_TOKEN=secrets.token_hex(32),AEGIS_DASHBOARD_TOKEN=secrets.token_hex(32),AEGIS_API_URL=f'http://127.0.0.1:{api_port}',AEGIS_GATEWAY_URL=f'http://127.0.0.1:{gateway_port}',WEBVIEW2_USER_DATA_FOLDER=str(SCRATCH/f'webview-{debug}'),WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f'--remote-debugging-port={debug} --remote-debugging-address=127.0.0.1')
 archives=SCRATCH/f'archives-{gateway_port}'; archives.mkdir()
 env['AEGIS_ARCHIVE_DIRECTORY']=str(archives)
 env['AEGIS_DESKTOP_PROFILE_DIR']=str(SCRATCH/f'desktop-profile-{gateway_port}')
 env['AEGIS_COMPANION_SETTINGS_PATH']=str(SCRATCH/f'companion-settings-{gateway_port}.json')
 with ExitStack() as stack:
  listeners=[stack.enter_context(socket.socket()) for _ in range(2)]
  for listener in listeners: listener.bind(('127.0.0.1',0))
  config=SCRATCH/f'sources-{gateway_port}.json'
  config.write_text(json.dumps({'sources':[{'deviceId':f'QA-DEVICE-{i:02}','baseUrl':f'http://127.0.0.1:{listener.getsockname()[1]}','tokenEnvironment':'AEGIS_EMULATOR_TOKEN','transport':'replay','pollIntervalMs':100,'equipmentId':'QA-SYNTHETIC','moduleId':f'MODULE-{i:02}'} for i,listener in enumerate(listeners,1)]}),encoding='utf-8')
  sources=load_sources(config); paths=ProcessCollector(sources,archives).archive_paths
  env['AEGIS_STORED_SOURCES']=json.dumps([{'storeId':f'qa-{i}','kind':'RAW_V1','path':path.as_posix()} for i,path in enumerate(paths.values(),1)])
  receipt=datetime.now(timezone.utc)-timedelta(minutes=5)
  expected=asyncio.run(fixture.seed(paths,sources,receipt)); original=fixture.evidence(paths)
  api=fixture.start_api('worksheet',api_port,config,archives,env,expected,receipt); stack.callback(fixture.stop_api,*api)
  log=stack.enter_context((OUT/'gateway.log').open('w',encoding='utf-8'))
  gateway=subprocess.Popen(['dotnet',str(ROOT/'src/Aegis.Gateway/bin/Release/net10.0/Aegis.Gateway.dll')],cwd=ROOT/'src/Aegis.Gateway',env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW); stack.callback(_stop,gateway)
  client=stack.enter_context(httpx.Client(timeout=2,trust_env=False))
  wait_until(lambda:client.get(env['AEGIS_GATEWAY_URL']+'/health').status_code==200,[gateway],seconds=25)
  static=ROOT/'src/Aegis.Gateway/bin/Release/net10.0/wwwroot'
  for file in (ROOT/'ui/dist').rglob('*'):
   if file.is_file(): assert file.read_bytes()==(static/file.relative_to(ROOT/'ui/dist')).read_bytes()
  assert client.get(env['AEGIS_GATEWAY_URL']+'/').content==(static/'index.html').read_bytes()
  with sync_playwright() as pw:
   if native:
    desktop=subprocess.Popen([str(ROOT/'src/Aegis.Desktop/bin/Release/net10.0-windows/Aegis.Desktop.exe')],cwd=ROOT,env=env);stack.callback(_stop,desktop)
    if preview:
     print('Synthetic native preview: closes after 180 seconds',flush=True)
     try: desktop.wait(timeout=180)
     except subprocess.TimeoutExpired: pass
     return
    wait_until(lambda:client.get(f'http://127.0.0.1:{debug}/json/version').status_code==200,[desktop],seconds=25)
    for attempt in range(3):
     try:
      browser=pw.chromium.connect_over_cdp(f'http://127.0.0.1:{debug}');break
     except Exception as error:
      if 'EFAULT' not in str(error) or attempt==2:raise
      time.sleep(.2)
    page=browser.contexts[0].pages[0]
   else:
    browser=pw.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1.25)
    page.goto(env['AEGIS_GATEWAY_URL'])
    page.get_by_label('로컬 접속 토큰',exact=True).fill(env['AEGIS_DASHBOARD_TOKEN']);page.get_by_role('button',name='대시보드 열기').click()
   errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
   modules=page.get_by_role('list',name='수집 모듈 목록')
   first=modules.get_by_role('button',name='MODULE-01',exact=False)
   expect(first).to_contain_text('복원 이력')
   first.focus();first.press('ArrowDown');page.keyboard.press('Enter')
   second=modules.get_by_role('button',name='MODULE-02',exact=False);expect(second).to_have_attribute('aria-pressed','true')
   table=page.get_by_role('table',name='태그 관측값')
   zero=table.get_by_role('row').filter(has_text='actualFlowLpm');expect(zero).to_have_attribute('data-state','RESTORED')
   assert zero.locator('.tag-value').evaluate('(e)=>e.firstChild.textContent')=='0'
   absent=table.get_by_role('row').filter(has_text='temperatureC');expect(absent).to_have_attribute('data-state','ABSENT');expect(absent.locator('.tag-value')).to_have_text('—')
   missing=table.get_by_role('row').filter(has_text='valveFeedbackPct');expect(missing).to_have_attribute('data-state','MISSING');expect(missing.locator('.tag-value')).to_have_text('—%')
   pick=missing.get_by_role('button');pick.focus();pick.press('Enter')
   expect(page.get_by_text('표시 범위에 사용 가능한 표본이 없습니다.',exact=True)).to_be_visible()
   archive=page.get_by_role('button',name='선택 태그 보관 이력');archive.focus()
   prefix='native' if native else 'browser'
   page.screenshot(path=str(OUT/f'{prefix}-worksheet.png'),full_page=True)
   archive.press('Enter');history=page.get_by_role('region',name='로컬 보관 수신 이력')
   expect(history.get_by_role('heading')).to_be_focused();expect(table).to_be_visible()
   expect(history.get_by_label('신호 키',exact=False)).to_have_value('valveFeedbackPct')
   with page.expect_response(lambda r:'/api/stored-records?' in r.url) as response:
    history.get_by_role('button',name='보관 이력 조회').press('Enter')
   assert 'deviceId=QA-DEVICE-02' in response.value.url and 'signals=valveFeedbackPct' in response.value.url
   expect(history.get_by_role('status')).to_contain_text('1개 보관 기록')
   record=history.get_by_role('row').filter(has_text='valveFeedbackPct');expect(record.get_by_role('cell').nth(1)).to_have_text('값 없음');expect(record.get_by_role('cell').nth(3)).to_have_text('MISSING')
   page.screenshot(path=str(OUT/f'{prefix}-archive.png'),full_page=True)
   history.get_by_role('button',name='선택 태그로 돌아가기').press('Escape');expect(archive).to_be_focused();expect(pick).to_have_attribute('aria-pressed','true')
   archive.press('Enter');expect(history.get_by_role('heading')).to_be_focused()
   history.get_by_label('수신 시작',exact=False).fill('2000-01-01T00:00:00Z');history.get_by_label('수신 종료',exact=False).fill('2000-01-01T00:01:00Z');history.get_by_role('button',name='보관 이력 조회').click();expect(history.get_by_role('status')).to_contain_text('0개 보관 기록')
   page.screenshot(path=str(OUT/f'{prefix}-empty.png'),full_page=True)
   history.get_by_label('수신 종료',exact=False).fill('1999-01-01T00:00:00Z');history.get_by_role('button',name='보관 이력 조회').click();expect(history.get_by_role('status')).to_contain_text('UTC 범위')
   page.screenshot(path=str(OUT/f'{prefix}-error.png'),full_page=True)
   history.get_by_label('수신 시작',exact=False).fill((receipt-timedelta(minutes=1)).isoformat());history.get_by_label('수신 종료',exact=False).fill((receipt+timedelta(minutes=1)).isoformat());history.get_by_role('button',name='보관 이력 조회').click();expect(history.get_by_role('status')).to_contain_text('1개 보관 기록')
   history.get_by_role('button',name='선택 태그로 돌아가기').click();expect(archive).to_be_focused()
   if not native:
    for width in (900,640,390):
     page.set_viewport_size({'width':width,'height':1000});page.screenshot(path=str(OUT/f'browser-{width}.png'),full_page=True)
     assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
    page.get_by_label('장비 · 모듈 검색',exact=True).fill('no-matching-module');expect(page.get_by_text('조건에 맞는 모듈이 없습니다.')).to_be_visible();page.screenshot(path=str(OUT/'browser-no-module.png'),full_page=True)
   if native:
    from native_window import check
    check(page,desktop.pid,OUT,expect)
   assert not errors,errors
   manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'src/Aegis.Desktop/bin/Release/net10.0-windows/Aegis.Desktop.dll',static/'index.html']}
   (OUT/f'{prefix}-checks.json').write_text(json.dumps({'result':'passed','keyboard':'Playwright dispatched keys; not physical Windows input','states':['restored zero','missing','absent','empty archive','invalid UTC range','retry'],'assetHashes':manifest,'pageErrors':errors},indent=2),encoding='utf-8')
   if not native: browser.close()
  assert fixture.evidence(paths)==original
  print('PASS exact assets; real two-module collector/Gateway/SQLite; zero/missing/absent; keyboard selection/archive/Escape/focus; empty/error/retry; original rows unchanged',flush=True)
if __name__=='__main__':main()
