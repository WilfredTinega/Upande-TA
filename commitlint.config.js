// Enforces Conventional Commits (https://www.conventionalcommits.org/), the
// same convention semantic-release uses to decide the next version:
//   fix:   -> patch (x.y.Z)
//   feat:  -> minor (x.Y.0)
//   chore/ci/docs/style/test/refactor/perf/build -> no release
// A "BREAKING CHANGE:" footer is allowed but (per .releaserc) does NOT force a
// major bump — majors are managed manually, mirroring ERPNext.
module.exports = {
	extends: ["@commitlint/config-conventional"],
};
