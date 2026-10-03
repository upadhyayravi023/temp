' start.vbs — KeyFlow agent silent launcher
' Double-click this file to start the KeyFlow agent in the background (no console window).

Dim oShell, oFSO, scriptDir
Set oShell = CreateObject("WScript.Shell")
Set oFSO   = CreateObject("Scripting.FileSystemObject")

scriptDir = oFSO.GetParentFolderName(WScript.ScriptFullName)

oShell.Run "pythonw """ & scriptDir & "\agent.py""", 0, False
