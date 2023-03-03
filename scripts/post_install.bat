call "%PREFIX%\Scripts\activate.bat"

echo|set /p="
call %PREFIX%\Scripts\activate.bat && python -m ecodata.app
" > "${PREFIX}"/app.bat
