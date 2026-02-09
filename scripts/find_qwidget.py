import re
p='e:/py/BaiLeHui_PanicButton/UI/scada_dialog.py'
with open(p,'r',encoding='utf-8') as f:
    for i,l in enumerate(f,1):
        if 'QWidget =' in l or 'as QWidget' in l or l.strip().startswith('def QWidget'):
            print(f"{i}: {l.rstrip()}")
# show any assignment to QWidget anywhere (including subtle ones)
with open(p,'r',encoding='utf-8') as f:
    s=f.read()
    if re.search(r"\bQWidget\s*=", s):
        print('Found assignment to QWidget')
    if re.search(r"except\s+.*\s+as\s+QWidget", s):
        print('Found except ... as QWidget')
    if re.search(r"def\s+QWidget\b", s):
        print('Found def QWidget')
