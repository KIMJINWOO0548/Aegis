"""Windows-only verification helpers; called only while the coordinated GUI slot is held."""
import ctypes,time,json
from ctypes import wintypes
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2]/'motor/.venv/Lib/site-packages'))
from PIL import ImageGrab
user=ctypes.WinDLL('user32',use_last_error=True)
user.SetProcessDpiAwarenessContext.argtypes=[wintypes.HANDLE]
user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
user.GetForegroundWindow.restype=wintypes.HWND
user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
user.GetDpiForWindow.argtypes=[wintypes.HWND]
user.SetForegroundWindow.argtypes=[wintypes.HWND]
user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]

def window(pid):
 result=[]
 callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
 @callback
 def visit(hwnd,_):
  owner=wintypes.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
  if owner.value==pid and user.IsWindowVisible(hwnd):result.append(hwnd)
  return True
 user.EnumWindows(visit,0)
 assert len(result)==1,result
 return result[0]

def capture(hwnd,path):
 rect=wintypes.RECT();assert user.GetWindowRect(hwnd,ctypes.byref(rect))
 ImageGrab.grab(bbox=(rect.left,rect.top,rect.right,rect.bottom)).save(path)
 text=ctypes.create_unicode_buffer(256);user.GetWindowTextW(hwnd,text,256)
 return {'dpi':user.GetDpiForWindow(hwnd),'physicalRect':[rect.left,rect.top,rect.right,rect.bottom],'title':text.value}

def key(hwnd,vk):
 assert user.GetForegroundWindow()==hwnd,'Physical key refused: owned window is not foreground'
 user.keybd_event(vk,0,0,0);user.keybd_event(vk,0,2,0);time.sleep(.12)

def check(page,pid,out,expect):
 hwnd=window(pid);user.SetForegroundWindow(hwnd);time.sleep(.3)
 assert user.GetForegroundWindow()==hwnd
 first=page.get_by_role('list',name='수집 모듈 목록').get_by_role('button',name='MODULE-01',exact=False)
 rect=first.bounding_box();origin=wintypes.POINT(0,0)
 user.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
 user.ClientToScreen(hwnd,ctypes.byref(origin))
 scale=page.evaluate('devicePixelRatio')
 user.SetCursorPos(origin.x+round((rect['x']+rect['width']/2)*scale),origin.y+round((rect['y']+rect['height']/2)*scale))
 user.mouse_event(2,0,0,0,0);user.mouse_event(4,0,0,0,0);time.sleep(.2)
 first.focus();key(hwnd,0x0D);expect(first).to_have_attribute('aria-pressed','true')
 key(hwnd,0x28);key(hwnd,0x0D)
 second=page.get_by_role('list',name='수집 모듈 목록').get_by_role('button',name='MODULE-02',exact=False);expect(second).to_have_attribute('aria-pressed','true')
 tag=page.get_by_role('table',name='태그 관측값').get_by_role('button',name='밸브 피드백 차트 선택');tag.focus();key(hwnd,0x0D)
 archive=page.get_by_role('button',name='선택 태그 보관 이력');archive.focus()
 metrics=capture(hwnd,out/'native-whole-worksheet.png')
 key(hwnd,0x0D);history=page.get_by_role('region',name='로컬 보관 수신 이력');expect(history.get_by_role('heading')).to_be_focused()
 history.get_by_role('button',name='보관 이력 조회').focus();key(hwnd,0x0D);expect(history.get_by_role('status')).to_contain_text('1개 보관 기록')
 metrics['archive']=capture(hwnd,out/'native-whole-archive.png')
 key(hwnd,0x1B);expect(archive).to_be_focused()
 page.get_by_label('장비 · 모듈 검색',exact=True).focus()
 key(hwnd,0x09);expect(second).to_be_focused()
 key(hwnd,0x09);expect(page.get_by_role('button',name='보관 이력',exact=True)).to_be_focused()
 key(hwnd,0x09);expect(page.get_by_label('태그 상태 필터')).to_be_focused()
 key(hwnd,0x09);expect(page.get_by_role('region',name='태그 관측값')).to_be_focused()
 key(hwnd,0x09);expect(tag).to_be_focused()
 key(hwnd,0x09);expect(archive).to_be_focused()
 metrics['tabRoute']='search → selected module → module archive → state filter → table scroll → selected tag → tag archive; six physical Tab keys'
 metrics['physicalKeys']='Windows keybd_event: module ArrowDown/Enter, tag Enter, archive Enter/query Enter/Escape; OS mouse click activates WebView; subsequent initial focus assigned via DOM'
 # Browser zoom affects only this app; no system DPI setting changes.
 key(hwnd,0x11)
 user.keybd_event(0x11,0,0,0)
 try:
  for _ in range(3):key(hwnd,0xBB)
 finally:user.keybd_event(0x11,0,2,0)
 time.sleep(.4)
 metrics['zoomViewport']=page.evaluate('({width:innerWidth,dpr:devicePixelRatio,scrollWidth:document.documentElement.scrollWidth})')
 metrics['zoomCapture']=capture(hwnd,out/'native-whole-app-zoom.png')
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 user.keybd_event(0x11,0,0,0)
 try:key(hwnd,0x30)
 finally:user.keybd_event(0x11,0,2,0)
 assert user.SetWindowPos(hwnd,None,0,0,850,800,0x0016)
 time.sleep(.4)
 metrics['narrowViewport']=page.evaluate('({width:innerWidth,dpr:devicePixelRatio,scrollWidth:document.documentElement.scrollWidth})')
 metrics['narrowCapture']=capture(hwnd,out/'native-whole-narrow.png')
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 (out/'native-window-checks.json').write_text(json.dumps(metrics,indent=2,ensure_ascii=False),encoding='utf-8')
 return metrics
