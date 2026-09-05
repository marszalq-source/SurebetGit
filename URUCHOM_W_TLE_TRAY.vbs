Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = ScriptDir

PythonExe = "pythonw.exe"
ScriptPath = ScriptDir & "\tray_launcher.py"

Cmd = Chr(34) & PythonExe & Chr(34) & " " & Chr(34) & ScriptPath & Chr(34)
WshShell.Run Cmd, 0, False
