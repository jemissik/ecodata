robocopy "%PREFIX%\*.*" "%PREFIX%\pkg" /MOV /E

(
echo echo Launching app...
echo call %PREFIX%\lib\Scripts\activate.bat
echo python -m ecodata.app
)>%PREFIX%\app.bat
pause