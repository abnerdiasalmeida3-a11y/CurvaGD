# Gera um unico executavel, CurvaGD.exe. Use "Gerar executavel.cmd", que
# compila e copia o EXE para a pasta de cima (ao lado desta pasta "codigo").
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = collect_data_files('pyogrio') + collect_data_files('holidays') + [
    ('app/config/schemas', 'app/config/schemas'),
    ('app/config/calendars', 'app/config/calendars'),
    ('app/config/municipios_ibge.json', 'app/config'),
]
binaries = collect_dynamic_libs('pyogrio')
hiddenimports = (collect_submodules('holidays.countries') + ['holidays.countries.brazil']
                 + collect_submodules('pyogrio', filter=lambda name: not name.startswith('pyogrio.tests')))
a = Analysis(['main.py'], pathex=[], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'geopandas', 'matplotlib',
                       'shapely', 'pyproj', 'pytest', 'tests', 'pvlib', 'py_dss_interface'],
             noarchive=False)
# DLLs ICU sem versao de outros programas no PATH (ex.: Poppler) sombreiam a API
# ICU nativa do Windows e impedem o QtCore de carregar.
a.binaries = [item for item in a.binaries if item[0].lower() not in {'icuuc.dll', 'icudt78.dll', 'icuin.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name='CurvaGD', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False)
