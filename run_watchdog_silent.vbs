Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir
pythonwPath = "C:\Users\Technolog\AppData\Local\Programs\Python\Python38\pythonw.exe"
If Not fso.FileExists(pythonwPath) Then
    pythonwPath = "pythonw.exe"
End If
cmd = """" & pythonwPath & """ """ & scriptDir & "\scanner_watchdog.py"""
WshShell.Run cmd, 0, False
