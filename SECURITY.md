# Security policy

## Reporting a vulnerability

Please report security problems privately, through GitHub's "Report a vulnerability"
on the Security tab of this repository, and not in a public issue.

Include what you found, how to reproduce it and which version you ran. Expect an
answer within a few days.

## What is in scope

- The integration code in `custom_components/aquastilla`.
- Anything that could leak the account's e-mail, password or token, for example
  through logs, diagnostics or error messages.
- Anything that could send a command to a device that the owner did not ask for.

## What is not

- The vendor's cloud service and the vendor's app. Report those to the vendor.

## Reports and logs

Diagnostics downloaded from Home Assistant are built from an allow-list and pseudonymise
device ids. Still, read a log or a diagnostics file before attaching it to an issue, and
remove anything that identifies you or your installation.
