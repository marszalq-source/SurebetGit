Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = ScriptDir
PythonExe = "C:\Users\Technolog\AppData\Local\Programs\Python\Python38\pythonw.exe"
ScriptPath = ScriptDir & "\sts_live_scanner.py"
WshShell.Run Chr(34) & PythonExe & Chr(34) & " " & Chr(34) & ScriptPath & Chr(34) & " --server", 0, False
