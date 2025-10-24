# bb_sync.spec - minimal PyInstaller spec
block_cipher = None
a = Analysis(['bb_sync.py'], pathex=[], binaries=[], datas=['.env'], hiddenimports=['requests'])
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='bb_sync', console=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='bb_sync')
