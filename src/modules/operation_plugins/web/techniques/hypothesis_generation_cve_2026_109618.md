---
schema_version: 1
title: PHP CVE-2026-109618 OOM-corruption applicability
purpose: Assess recorded PHP patch, SAPI and exposure facts without inducing OOM or claiming RCE from
  applicability.
applicability:
- Recorded evidence identifies PHP request handling, or CVE-2026-109618 applicability is assigned.
phases:
- hypothesis_generation
- analysis
active: true
triggers:
- phases:
  - hypothesis_generation
  when: Recorded PHP serving or patch evidence raises a CVE-2026-109618 applicability question needing
    future prerequisite checks.
  request: 'PHP CVE-2026-109618 OOM-corruption applicability: hypothesis generation'
- phases:
  - analysis
  when: Recorded PHP version, SAPI, patch or mitigation evidence needs interpretation for CVE-2026-109618.
  request: 'PHP CVE-2026-109618 OOM-corruption applicability: analysis'
sources:
- url: https://github.com/php/php-src/security/advisories/GHSA-w8hw-w9fx-j79w
  title: RCE via OOM corruption
  retrieved_at: '2026-11-06T21:28:17.730513+00:00'
  selection_reason: PHP maintainer mechanism, fixed versions, SAPI susceptibility confidence and incomplete
    configuration mitigation.
- url: https://github.com/php/php-src/security/advisories/GHSA-6wmf-4rv5-p3rp
  title: PHP session upload progress advisory
  retrieved_at: '2026-11-06T21:28:17.730603+00:00'
  selection_reason: Related maintainer advisory distinguishes CVE-2026-109619 lifecycle and upload-progress
    conditions.
- url: https://cveawg.mitre.org/api/cve/CVE-2026-109618
  title: CVE-2026-109618 CNA record
  retrieved_at: '2026-11-06T21:28:17.730723+00:00'
  selection_reason: PHP CNA identity and affected-version metadata for CVE-2026-109618.
source_coverage: Current retained PHP maintainer advisories and CNA record support source-required mechanism,
  fixed-version, mitigation and SAPI claims. They do not establish target exploitability. Original controls
  are knowledge-allowed synthesis; sibling-CVE details are used only to prevent conflation. No OOM execution
  procedure is included.
---

# PHP CVE-2026-109618 OOM-corruption applicability

## Applicability and prerequisites

This non-executing method uses recorded facts to design future prerequisite checks and analyze
applicability. It never induces memory exhaustion, changes PHP limits, submits oversized multipart
bodies or runs an exploit. Do not advertise vulnerability verification or execution from these
applicability checks. Use equivalent evidence readers; no required scanner exists.

PHP's maintainer advisory GHSA-w8hw-w9fx-j79w identifies CVE-2026-109618 as invalid request state
consumed during shutdown after OOM interrupts initialization through `longjmp()`. Fixed supported
branch releases are 8.2.35, 8.3.36, 8.4.27 and 8.5.12. Compare versions within the correct branch;
use numeric version components and vendor backport evidence, never lexical string ordering.
Earlier unsupported branches require their own vendor patch evidence rather than an invented fix.

The advisory reports confirmed application-independent HTTP RCE for mod_php. FPM exits a child
on request-startup failure; application-specific OOM remains a source-analysis susceptibility,
without a complete demonstrated FPM exploit for this CVE in the retained advisory. FrankenPHP
is likely susceptible by analogous shutdown behavior; LSAPI application-dependent exploitation
is likely but unvalidated. Do not flatten those confidence levels into universal demonstrated RCE.

`memory_limit=-1` removes one predictable PHP-limit OOM path, not underlying allocation failure,
and is explicitly incomplete mitigation. It can change host resource-exhaustion risk; this method
never recommends changing it during assessment. Updating with the fix is the complete mitigation
identified by the maintainer. Session upload-progress and fatal-backtrace settings concern distinct
state-lifecycle paths in CVE-2026-109619 and are not universal prerequisites for CVE-2026-109618.

## Hypothesis generation — recorded evidence only

1. Identify the exact interpreter question from an existing HTTP interaction or assigned component
   record. Separate PHP presence from a loaded build, the web server banner from SAPI, and a local
   tool's PHP version from the remote interpreter. The latter is not target evidence.
2. List known and missing conditions: branch/build or vendor backport, request-processing SAPI,
   route reachability, patch state and applicable request-handling behavior. Identify the source
   of each fact. An ordinary login page does not show whether PHP startup occurs before login.
3. Design future non-destructive information checks. Prefer owner-supplied runtime/package and
   patch records tied to the serving pool, or already exposed authorized response metadata.
   Do not deploy phpinfo, seek target configuration files or run local target commands. The web
   module grants external protocol access, not local filesystem/process access.
4. Define accepted evidence baselines: an identified branch and pool with credible patch provenance,
   and an unchanged benign request showing the assigned route's ordinary behavior. Version and
   deployment statements from unrelated pools are not a valid comparison.
5. Define controlled analysis variations against that baseline: branch-correct affected/fixed
   version classification, a verified backported build, and alternative documented SAPI behavior.
   Negative controls include an unrelated version source or unverified banner; these must remain
   insufficient rather than being accepted as proof. No malformed request or resource load is used.
6. State conditional hypotheses separately. An affected loaded interpreter supports a patch
   applicability concern. A reachable mod_php startup path supports that documented attack-path
   premise. Neither independently demonstrates a successful OOM, memory corruption or RCE.
7. Record what could contradict each premise: authoritative fixed build/backport evidence, absence
   of PHP in the actual route, or verified inability of the tested actor to reach a specific path.
   Authentication, restricted internet exposure and incomplete mitigations narrow exposure; they
   do not establish the interpreter is fixed or all paths are safe.

## Analysis — interpret existing applicability evidence

1. Reconcile route, pool, timestamp, branch and patch provenance. Confirm the compared version is
   the serving interpreter. If only a banner exists, preserve confidence limits and vendor backports.
2. Compare numeric versions within the branch and independently assess the applicable SAPI path.
   Do not import the confirmed mod_php exploit status into FPM, LSAPI or FrankenPHP conclusions.
3. Keep `memory_limit=-1`, request-size controls and upload-progress settings in their actual scope.
   None is a blanket refutation of CVE-2026-109618; disabling upload progress does not remove all
   OOM strategies. A blocked request or absent error log is not proof the vulnerable code is absent.
4. Produce a premise-level result: supported affected-build concern, contradicted tested premise,
   or unresolved due to missing facts. Explicitly leave runtime exploitation untested. Any future
   OOM or memory-corruption work needs separate safe-environment methodology and authorization.

## Evidence requirements and limitations

Retain references to build/backport provenance, its serving-pool association, recorded route and
identity context, SAPI evidence, timestamps and the scope of every mitigation claim. Identify
unknown pool heterogeneity and external-visibility gaps. Do not copy credentials or operation-local
applicability appendices into reusable guidance. No cleanup is needed because this method creates
no target state; it must not alter limits, configuration, workers or packages.

External testing cannot establish every binary, configuration override or allocation path. The
maintainer descriptions may evolve; source-sensitive deployment conclusions are bounded to the
retained advisory. An applicability concern is not exploit proof, a denial-of-service result or
ATT&CK behavior. Do not substitute an inferred lack of exposure for confirmed patching.
