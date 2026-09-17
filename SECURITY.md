# Security

HelixHR is an employee-facing portal in front of HR data. We take reports
seriously and would rather hear about a problem privately than read about it.

## Reporting a vulnerability

Email **dev@nitcoinc.ai** with:

- what you found and where (a method name, a route, a file);
- how to reproduce it, as precisely as you can;
- what you think the impact is.

Please do not open a public GitHub issue for a security problem. You will get
an acknowledgement, and we will keep you informed as we work on a fix. We are
happy to credit you in the release notes if you would like.

## Scope

In scope: anything in this repository — the Frappe app under `helixhr/`, the
Vue frontend under `frontend/`, the fixtures and patches it ships, and the
build and deployment guidance in `docs/`.

Out of scope: vulnerabilities in Frappe, ERPNext or HRMS themselves, which
should go to [Frappe's security process](https://github.com/frappe/frappe/security),
and issues that require a misconfigured site the preflight already FAILs on
(see the README's Configure table).

## What is already in place

- Every server call runs as the signed-in Frappe user; the browser never holds
  a token or API key. Frappe's own permissions are the security model.
- A go-live preflight (`bench --site <site> execute helixhr.preflight.run`)
  machine-checks the per-site settings that matter — strict User Permissions,
  signup disabled, upload policy, CSRF, per-user write limits, and a real HTTPS
  probe of the security headers and session cookie flags when
  `helixhr_public_url` is set.
- Per-user rate limits on every write and on every read that fans out per
  employee. Uploads are allow-listed by type and size and served as downloads.
- No source maps in production builds, content-hashed assets, no service
  worker, nothing personal cached offline.
- Punch coordinates carry a retention period and a daily erasure job.
- The optional install ping is off by default and, when enabled, carries no
  employee, user or company data — see the README.
