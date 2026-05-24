$WshShell = New-Object -ComObject WScript.Shell
$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "AJA - START.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "d:\AgenticAI\Project1(no-name)\START.bat"
$Shortcut.WorkingDirectory = "d:\AgenticAI\Project1(no-name)"
$Shortcut.Description = "Launch AJA Master Orchestrator"
$Shortcut.IconLocation = "shell32.dll,24"
$Shortcut.Save()

Write-Host "Shortcut created on Desktop: $ShortcutPath"
