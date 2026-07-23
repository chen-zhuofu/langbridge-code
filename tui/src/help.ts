export const HELP_TEXT = `Commands:
  /help              show this help
  /new               start a new session
  /sessions          open the session picker (Ctrl+R)
  /resume [n]        open the picker, or resume session number <n>
  /delete <n>        delete session number <n>
  /approve [on|off]  approve a pending action, or toggle auto-approve (yolo)
  /yolo [on|off]     toggle yolo mode (auto-approve all write tools)
  /deny              deny a pending action
  /pause             pause / resume the running agent
  /stop              stop the current turn
  /queue             show queued messages waiting to run
  /queue clear       drop all queued messages
  /goal <condition>  work autonomously until the condition is met
  /goal              show active goal status
  /goal clear        remove the current goal
  /goal pause        pause goal auto-continue
  /goal resume       resume a paused goal
  /banner [on|off]   show or hide the header box (Ctrl+B toggles)
  /copy              copy the last assistant reply to the clipboard
  /<skill> [args]    invoke a skill (e.g. /grilling, /writing-simple-plans)
  /exit              quit

Keys: Enter send · Shift+Enter / Ctrl+J newline
      Home/End · Ctrl+W delete word · Ctrl+U/K clear line
      Ctrl+A approve · Ctrl+D deny · Ctrl+Y yolo · Ctrl+P pause · Ctrl+S stop
      Ctrl+R sessions · Ctrl+B header · Ctrl+E select/wheel · Ctrl+O copy
      PageUp/PageDown or Ctrl+↑/↓ scroll · Ctrl+C quit
While the agent is busy, Enter queues your message; queued messages run only
after the current turn finishes successfully (not after /stop or errors).`;
