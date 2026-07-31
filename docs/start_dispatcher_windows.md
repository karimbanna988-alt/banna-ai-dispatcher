# Starting and Stopping the Dispatcher (Windows)

You never need to open a black command-window ("CMD") yourself. Use
these four files instead. They live in the `tools\windows_launcher`
folder inside the project.

## To start working

1. Double-click **Start Dispatcher**.
2. Wait a few seconds -- a window will show short status messages, then
   your web browser will open automatically to the login page.
3. Log in with the usual password.

That's it. You can close the small black status window once it says
"Press any key to close this window" -- the dispatcher keeps running
in the background even after you close that window.

## To check whether it's already running

Double-click **Dispatcher Status**. It will tell you whether the
dispatcher and the tunnel are currently running, and show the current
web address if one is known.

## To reopen the page without restarting anything

If the dispatcher is already running and you just closed the browser
tab, double-click **Open Dispatcher** -- it reopens the same page
without starting a second copy of anything.

## To stop for the day

Double-click **Stop Dispatcher** when you're intentionally shutting
down. This closes the dispatcher and the web tunnel cleanly.

## A few things worth knowing

- Double-clicking **Start Dispatcher** again while it's already running
  is safe -- it notices the dispatcher is already up and will not start
  a second copy.
- **Start Dispatcher** never enters your password for you -- you still
  type it yourself on the login page, every time.
- Closing the small black status window does **not** stop the
  dispatcher. Use **Stop Dispatcher** for that.
- If something looks wrong, **Dispatcher Status** is the safe first
  thing to check -- it never starts, stops, or changes anything, it
  only reports.

## If something goes wrong

- If the browser page never opens, run **Dispatcher Status** to see
  whether the dispatcher actually started.
- If it says the tunnel didn't get a web address yet, wait about 20
  seconds and check **Dispatcher Status** again.
- If nothing seems to be starting at all, someone technical can look at
  the log files under `.deploy_logs\launcher\` inside the project
  folder -- those never contain your password or any customer
  information.
