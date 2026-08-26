Set-Location "D:\AgenticAI\Project1(no-name)"
Get-Content ".opencode\night-shift\agy\prompts\test-triage.md" -Raw | & "E:\agy\bin\agy.exe" --model gemini-3.7-flash-low --effort low --dangerously-skip-permissions --output-format json --print-timeout 20m > ".opencode\night-shift\agy\test-triage.out.json" 2> ".opencode\night-shift\agy\test-triage.err.log"
