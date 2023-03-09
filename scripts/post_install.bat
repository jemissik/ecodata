Rem robocopy "%PREFIX%\*.*" "%PREFIX%\pkg" /MOV /E

(
echo echo Launching app...
echo call %PREFIX%\Scripts\activate.bat
echo python -m ecodata.app
)>%PREFIX%\ecodata.bat

(
echo echo Launching app...
echo call %PREFIX%\Scripts\activate.bat
echo python -m ecodata.app
)>%USERPROFILE%\Desktop\ecodata.bat