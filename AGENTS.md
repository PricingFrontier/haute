Priotise user experience and engineering quality over quick fixes and band aids.
We want the highest quality codebase.

For each individual item on a code change plan, use 2 agents, 1 developer, 1 reviewer.

Follow test driven development, for each planned functionality, think of all the complicated things a user might build and the edge cases and then build out the test suite to cover all of these. Then go ahead and build functionality.

After each complete functionality is built, create a team of agents to go over and review and make sure it has been built to the highest engineering standard.

Don't add uneccessary fallbacks, code should be allowed to fail loudly from which we can fix the errors, which is far superior to a fallback which is incorrect and hard to notice.

