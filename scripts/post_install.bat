call "%PREFIX%\Scripts\activate.bat"
python -m pip install --no-cache-dir --upgrade --force-reinstall --no-deps "git+https://github.com/%REPO%@%ECODATA_INSTALL_BRANCH%"
if errorlevel 1 exit /b %errorlevel%

python -c "import ecodata; from ecodata.app.apps import applications; print('Installed ecodata:', getattr(ecodata, '__version__', 'unknown'), ecodata.__file__); print('Registered apps:', ', '.join(sorted(applications))); assert applications"
if errorlevel 1 exit /b %errorlevel%
