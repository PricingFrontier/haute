# First-time walkthrough

A pricing analyst opens the tool for the first time, with no one to ask.

- All three drafts were present. I read the team brief and the three drafts, and nothing else.
- I used only what the drafts say is on screen and what they say happens. Where no control is named I write "not drawn". That gap is the finding.
- `04:25` means line 25 of the workflow draft. `05:` is the analytics draft and `06:` the UI draft.
- I make no claim about Haute's code. Any "today" below is a draft's claim, not mine.
- Scores are 1 to 5. **Intuitive:** could I tell the next step from the screen? **Effective:** did it make the model better, or stop a mistake?

## Task 1. A new node, 800,000 rows: a first comparison of two model types

1. Double-click the node's card (06:218). State 1 opens with `! Target missing` unfolded.
2. Pick what the target is: `(count) (amount) (rate) (yes/no)` (06:107-108). Then exposure, offset and loss in the same section (06:80). Read the handling sentence.
3. Open `! Split missing`. Press `Use` on the suggested split: policies whole, latest period as test rows (04:21, 06:353).
4. State 2. The list is sorted `By strength`. Arrow down a few factors. `Checks 2` is showing.
5. Click `[+ Add a model type]`, tick t-boost and GLM, press `Add and train` (06:63).
6. Open the Train list to watch the queue (state 6). Charts fill as each finishes (04:26).
7. Press `C` for Compare and read the standings (state 4).

- **Hesitated: count or rate?** My target is claims with an exposure column. The handling sentence calls that "claims per vehicle-year" (04:18), which reads as a rate. The choice comes before the sentence that would settle it.
- **Hesitated: column or type first?** State 1 draws the four types and no column picker (06:106-108).
- **Hesitated: is the split final?** "It fixes the split once" (04:19), yet "Setup was wrong. Change it" (04:238). `Use` does not say what I can undo.
- **Hesitated: sample or full?** The tool picks "from how long a training takes" (04:28-29). Nothing has trained yet, and no rule is given. The pick shows in the Train list (06:184, 06:496). The drafts do not say that I see it before `Add and train` starts. At 800,000 rows I cannot guess it.
- **Misread: the chip's percentage.** `t-boost 96%` is its place on the span (06:62). `t-boost 40%` in state 6 sits beside "round 212, about 2 min" (06:179-181), so it reads as progress. The node's card says `t-boost 40%, 2 waiting` (06:224). One slot, two meanings.
- **Misread: "depth (Quick) (Thorough)"** (06:184). I read depth as tree depth. Thorough means five folds (05:98), and no screen says so.
- **Surprised:** I asked for two trainings and got four. The two yardsticks train unasked (04:26-28), and the queue wireframe does not draw them (06:181-183).
- **Hesitated: compared against what?** The default reference is "the last logged run, else Default boost" (06:64). State 2 draws `vs No factors` (06:115). On a new node the drafts do not say which my first differences are taken from, or whether my two models are compared with each other at all (see task 5).
- **Unclear terms:** yardstick, the span, quick, thorough, "the handling".
- **Drafts disagree:** the target is "a count, an amount, a rate or yes/no" (04:16-17), or claim counts, claim sizes, pure premium and yes/no (05:14-18). "Rate" has no row in the second, and "amount" covers two. The Rows section holds "rows, exposure and claims" (06:81) or "rows, mass and events" (05:87). The workflow says "add model types and press Train" (04:25); the UI has one button.
- **Too many steps:** none. About nine actions.
- **Scores:** intuitive 4, effective 4. The empty state names the next step, and the fixed split and yardsticks stop real mistakes.

## Task 2. Find the factor the best model fits worst, and decide what to do

1. In Compare, find the best model. t-boost and LightGBM are bracketed as tied (06:132-133). I take t-boost: "between tied models the simpler one wins" (04:86).
2. Press `1` to choose t-boost, then `F`. The list re-sorts `By misfit` for the chosen model (06:92).
3. The top factor is `veh_age ###`. Its chart is already drawn. The figures line reads "2 of 14 levels are outside the range, about 1 expected by chance" (06:46-47).
4. Press `E` for its effect, `V` to flip to training rows, `S` to split by period.
5. Decide. Not drawn for a tree model. See below.

- **Hesitated: which model is best?** The bar shows LightGBM at 100% and t-boost at 96%. Only the `[` and `=` marks say tied. The "simpler wins" rule is in the workflow draft and on no screen.
- **Hesitated: what now?** This is the big one. The GLM gets "Suggests: a shape" (04:165). For t-boost the screen offers `[Try without] [Try v]` (06:47), and the `Try v` list is never spelled out. The moves exist across the drafts: a direction, by year, an interaction pair, a re-banding upstream (04:66-67, 04:118). Nothing links a misfit to one of them. "Suggestions" are promised in the factor's block (06:356) and not drawn in it (06:45-50).
- **Misread: "3 clear, 3 by chance"** (06:44-45). I read it as six factors: three real, three not. It means three clear misfits, and about three expected by chance alone (05:126). So the example says the list may be all noise, while the bars say "work here".
- **Misread: the list's marks.** `r`, `.`, `-` and `+` beside the names (06:49-53) are not in the legend (06:35-37).
- **Unclear terms:** misfit; "the range" (three kinds: 05:41-44); "clear".
- **Drafts disagree: what misfit is.** "Share of the chosen model's misfit, counting only misfit beyond its range" (04:48-49). Or the fall in the score from correcting the model by level on other rows, "as a share of the model's gain over the floor" (05:123). Two numbers. The bar has no unit (06:93).
- **Drafts disagree: left-out factors.** The measure "works for a factor that is left out, which is how a missed factor reaches the top" (05:125). The wireframe puts `colour -` at the bottom with no bar (06:52). I take `-` to mean left out; the legend does not say.
- **Not drawn:** the Checks pane has no wireframe, so neither has the readout at its top (06:363).
- **Too many steps:** finding takes two keys. Deciding takes a tour of five places.
- **Scores:** intuitive 3, effective 4. Finding is excellent. Deciding is left to me.

## Task 3. Test whether leaving out one factor hurts, without disturbing the other models

1. Choose the factor in the list. Choose the model with its number key.
2. Press `T`, "Try without it" (06:318), or click `[Try without]` in the All models block.
3. A what-if run trains beside the model's run, "and nothing goes out of date" (04:52).
4. The notice line: "What-if: t-boost without ncd_years. Clearly worse: -2.1 (-2.9 to -1.4)" (06:190).
5. Click `[What changed]` for settings, scores, fit moved and predictions (06:193-197).
6. Press `[Drop]`.

- **Hesitated: how long?** The workflow says Try "asks one model or all, and shows the time first" (04:305-306). The UI counts one key, with no prompt (06:340). On full rows I would not know if I had started three seconds or four minutes.
- **Hesitated: `T` beside `X`.** `X` leaves the factor out for every model (06:316). `T` tests it for one. The two sit two rows apart in the key table. `X` is the key that breaks the task, and only a toast says so.
- **Hesitated: Apply.** "Applying a shared change tried on one model puts the others out of date" (04:71). The `[Apply]` button does not say this (06:191). The task's promise holds until the one click that looks like the happy ending.
- **Hesitated: how long a result lasts.** It stays "until the settings or data change" (04:70). Does changing any other setting remove it?
- **Misread: the standings column.** The what-if row sits under `vs reference` and reads "clearly worse" (06:135). State 7 judges a what-if against its own model's run (06:193). The drafts do not say which the standings cell uses.
- **Unclear terms:** `[Keep]` (06:135), `[Keep as a reference model]` (06:191) and "Keep" (04:70). I assume one action.
- **Drafts disagree:** the prompt and the time, as above.
- **Too many steps:** none. Two keys.
- **Scores:** intuitive 4, effective 4. The best-designed task of the seven. The unmarked Apply costs it a point on each.

## Task 4. Fix one level of a table by hand, and find out what that cost

1. On the main screen the factor's block reads `fixed value: none` (06:46). I nearly click it. That is a value for every model type, against a base level I name (04:129-130).
2. Instead press `O` to open t-boost's table for this factor (state 5a).
3. Click the level, type the value, and type why (06:343). The row reads `Set [0.66] Line Smooth Monotone Cap Floor` (06:155).
4. Read the preview (06:156-159).
5. For the cost: not drawn. I press `[Try this edit]` and read `What changed` in Compare.
6. Press `[Apply]`.

- **Hesitated: two homes for one job.** "Fixed value" in All models, "hand edit" in Tables, and a third word, "set" (06:498). No screen says a fixed value is the business's rule and a hand edit is my judgement.
- **Hesitated: Try or Apply?** An edit here applies "at once with no training" (04:140). So both cost nothing, and their difference is not explained.
- **Hesitated: what does 0.66 mean?** The table is centred on its average (05:134). I think in relativities to a base level. "A named base level is an option" (05:135) and is not drawn. A fixed value uses a named base (04:130). Two scales for one table.
- **Misread: "on the rows you were looking at"** (06:157). I took it for the rows the edit touches. It is a caution that I have seen these validation rows (05:279).
- **Misread: a contradiction.** "Nothing else moves" (06:156), then "will disturb: veh_age, region" (06:158). The second means "would move if this were a refit" (05:277).
- **Unclear terms:** Line, Smooth, Monotone, Cap, Floor are defined nowhere. "Laid on top". "Spread across folds". `[Rebalance]` means rescale the base here (06:158, 05:283) and a learned scale factor for a boosted model elsewhere (06:375, 05:172).
- **Drafts disagree: the cost.** The analytics draft defines it: the difference with and without the edit, on the rows touched and overall, and the share of expected that moves (05:286-289). The UI says it lives in the Tables pane (06:366). State 5a draws none of it.
- **Drafts disagree: the standings.** A preview gain "never counts as an improvement in the standings" (05:279). `[Try this edit]` puts a what-if row in the standings (04:70, 06:155).
- **Missing advice:** "judgement edits last" (04:132) and "no clear difference is the good answer" (05:289) are on no screen.
- **Too many steps:** the edit is five. The cost is two more panes away.
- **Scores:** intuitive 2, effective 3. The edit is easy. Its cost, which is the point, is not on the screen.

## Task 5. Decide which of two models is better, and whether the gap is real

1. Press `C`. Read the two rows: t-boost `96%  +3.1 (+1.9 to +4.4)`, LightGBM `100%  +4.2 (+2.8 to +5.5)` (06:132-133).
2. Both differences are against Last logged (06:130). The gap between the two is not shown.
3. Read the `[` bracket: tied. Read the bar's end: "Gaps under 1.5 cannot be seen" (06:139).
4. To see the gap itself: choose t-boost, open the `vs` list and pick LightGBM as the reference (06:65).
5. Open `(Double lift)` and `(Where and why)` under "A against B" (06:140).
6. Set the reference back to Last logged.

- **Hesitated: A or B?** "A click on a row makes it A or B" (06:126-127). Which one, and how do I pick the other?
- **Hesitated: step 4 changes everything.** The reference is linked everywhere (06:23, 06:64). To compare two models once, I change every difference and every dashed line, and must remember to undo it.
- **Misread: the units.** `gain 96%`, `+3.1`, `off level 0.1`, "under 1.5", the card's `gain 3.4` (06:224) and "expect a gain of 3.0 to 4.6" (06:212). The header says "gain as a share of the span". Then LightGBM should be +7 against a reference at 93%, not +4.2. I cannot work out the unit from the example.
- **Misread: "gain".** The column holds the place on the span, which the analytics draft calls "position" (05:164). Its "gain" is the floor's score minus the model's (05:24).
- **Unclear terms:** "off level"; "the span"; "tied" against "no clear difference". Two phrases, one verdict.
- **Drafts disagree:** the reference picker offers "the run it replaced" (05:343), and the UI rules that out (06:436-437). The standings columns are gain, balance, off level, ranks risks and Gini (05:373); state 4 draws three of the five, plus `vs reference` (06:131). The target is "claims, count" (06:51), yet CatBoost "trained on Tweedie, not Gamma" (06:138).
- **Too many steps:** six for the commonest question in the tool, two of them undoing.
- **Scores:** intuitive 3, effective 5. A range on the difference, the tie bracket and "what the data can show" are the best things in the design. They stop me chasing noise.

## Task 6. Log the model, and say what was logged and on which rows it was judged

1. Choose t-boost and press `Log`. The panel replaces the settings column (state 8).
2. Step 1 reads "The run: full, thorough" (06:205). A refit has started unasked (04:183).
3. Step 2: review the last two factors, look at two checks, confirm the edits (06:206-209).
4. Step 3: read "Refit against reviewed" in the pane. One flag: region moved 2.6% (06:207).
5. Step 4: read "expect a gain of 3.0 to 4.6". Press `[Unseal and score]` (06:212-213).
6. Step 5: add why. Press `[Log to MLflow]`.

- **My answer, what was logged:** a refit of t-boost on the training and validation rows together, with the same settings and the hand edits (04:183-187), its boosting rounds fixed at the reviewed fit's best round (05:112). Not the run I reviewed.
- **My answer, where it was judged:** once, by the refit, on the test rows (05:106). Everything I reviewed came from the earlier fit, on the validation rows (05:112).
- **Which half the screen shows:** "Test rows sealed, looks: 0" and the pane title "Refit against reviewed" (06:205-213). That a refit is what gets logged, and on which rows it was fitted, is only in the prose of drafts 04 and 05. From the panel's "The run: full, thorough" alone I would answer wrongly.
- **Hesitated: opening Log starts a training.** On 800,000 rows that is minutes. Does `[x]` cancel it? Does Unseal wait for it? The refit's progress is asked for (04:338) and not drawn.
- **Hesitated: can I log without unsealing?** "Nothing blocks" (06:201). If so, what is recorded as the rows it was judged on?
- **Hesitated: "2 outstanding"** (06:215). Two factors, two checks, or the flag and the seal?
- **Misread: "Refit against reviewed . validation rows . in-sample"** (06:205). Validation rows and in-sample look like a contradiction, until I know the refit learned from them.
- **Unclear terms:** "band" has six meanings in the drafts: a factor's bands, a lift band, a drawn range, the band a test score should fall in, a banded GLM term, and t-boost's banding. "Looks". "Beyond its spread".
- **Drafts disagree:** the workflow asks for the refit's progress and the below-band choices (04:338-340); state 8 has neither. At unsealing the analytics draft shows balance too (05:106); the UI does not list it (06:201-202). Last logged should carry the in-sample mark where it learned from today's validation rows (05:80); its row has none (06:136).
- **Not drawn:** any receipt after logging. The model card (04:196) has no row in "Where everything lives".
- **Too many steps:** none. The step list is the right shape.
- **Scores:** intuitive 3, effective 4. Sealed rows, the band stated first and refit against reviewed stop real mistakes. The panel never says the one sentence the task asks for.

## Task 7. A month later: what state is everything in, and what was logged?

1. On the pipeline, read the card: "logged", "t-boost: claims per vehicle-year", "logged 12 Nov . gain 3.4", "3 settings changed since" (06:222-225).
2. Open the node. Read the chips for each model's state, the count on `Train`, and the count on `Checks`.
3. Read the notice line, if it has news (06:67).
4. For what was logged: press `C`. Last logged is a standings row and the default reference (06:64, 06:136).
5. For what changed since: open `(What changed)`, with A as t-boost's run and B as Last logged.
6. For each model's past: open its History pane, one model at a time.
7. For the model card: not in the UI. I would open MLflow.

- **Hesitated: there is no "where things stand".** The state is spread over the chips, two counts, one notice, a History pane per model and the Rows section. The card is the best summary, and it is outside the Modelling UI.
- **Hesitated: the notice.** It is "one line, only when there is news" (06:67), with an `x` (06:42). The workflow wants rows, periods, columns and new levels (04:200-201). If I close it, where is it again? "Changed data is a notice" (06:365) is the only answer.
- **Hesitated: "3 settings changed since".** Since the logged run, in the config or in a trained run? It has no link to the list.
- **Hesitated: the card when data changes.** Its states are "nothing logged, logged, changed since logged, training, error" (06:219). Is new data "changed since logged"?
- **Hesitated: my what-if runs.** They last "until the settings or data change" (04:70). After a month of new data they may be gone, unannounced.
- **Hesitated: "judge the old model first"** on the period just released from the test rows (04:206-207). I see no control for it. The rows switch never offers test rows (06:291), and no filter for that period is drawn.
- **Misread: "gain 3.4" on the card.** I assumed the test rows. It does not say.
- **Unclear terms:** "out of date" against "changed since logged" against "not trained here".
- **Drafts disagree:** a "banner" (04:200) against a one-line notice (06:67). "The model card is complete without the tool" (04:226), and the UI gives it no place.
- **Too many steps:** seven, across five places, to answer two questions.
- **Scores:** intuitive 2, effective 3. The refresh workflow is sound on paper. The screen does not lead me into it.

## Scores in one table

| Task | Intuitive | Effective | Why, in a line |
|---|---|---|---|
| 1. First comparison | 4 | 4 | The empty state leads. The run pick and the chip's % confuse |
| 2. Worst-fitted factor | 3 | 4 | Finding is two keys. Deciding has no guide for a tree model |
| 3. Leave one factor out | 4 | 4 | `T` is right. Apply hides its effect on the other models |
| 4. Hand edit and its cost | 2 | 3 | Two homes, a contradiction, and the cost is not drawn |
| 5. Which model is better | 3 | 5 | The range on the difference is the best idea. Units and A/B are not clear |
| 6. Log | 3 | 4 | Good steps. The panel never says what is logged |
| 7. A month later | 2 | 3 | No single place for where things stand |

## Words that tripped me, across the drafts

- **Band:** six meanings (task 6). Keep "band" for a factor's bands. Call the test one "expected range".
- **Gain:** the floor's score minus the model's (05:24), a place on the span (06:131), and a bare number (06:224).
- **Range:** three kinds (05:41-44). The UI labels two (06:272).
- **Mass and events** (05) against **exposure and claims** (04, 06). A pricing analyst says exposure and claims.
- **Yardstick, reference model, reference, benchmark.** Four words in one `vs` list. Yardstick is not the owner's word.
- **Quick, thorough, depth.** Never defined on screen.
- **Fixed value, hand edit, set.** Three words for a value I typed.
- **Tied** and **no clear difference.** One verdict.
- **Rebalance.** Two different actions.

## The ten fixes that would help most

Each quotes the line it changes. Figures inside new wording are made up, to show the shape.

1. **Say what is logged, in the Logging panel and after.** Changes 06:205, "1 The run   full, thorough". New: "1 What is logged: a refit of t-boost on training and validation rows (515k), same settings, 2 hand edits. Refitting, about 4 min." Step 4 adds "judged once, by the refit, on the 98k test rows". After Log, a receipt of the same two sentences stays on the node's card and on the Last logged row.
2. **Give every number in the standings a unit, and make the example add up.** Changes 06:131, "gain  vs reference", and 06:224, "logged 12 Nov . gain 3.4". Head the columns "place on span" and "difference, points of span". Reconcile the rows (100% against 93% cannot be +4.2). The card names its rows: "test rows: 3.4 points over the last logged run".
3. **A head-to-head that does not move the reference.** Changes 06:126-127, "A click on a row makes it A or B". New: a click makes a row A. A `B` button on any other row makes it B for the Compare pane only. Two bracketed rows show their own gap on the bracket: "t-boost against LightGBM: no clear difference (-1.1, -2.6 to +0.4)". Print the tie rule beside it: "tied: prefer the simpler".
4. **One misfit measure, with honest words in the list's header.** Changes 04:48-49, "sorts by each factor's share of the chosen model's misfit, counting only misfit beyond its range", to the analytics draft's measure (05:123). Changes 06:44-45, "3 clear, 3 by chance", to "3 clear misfits. Chance alone gives about 3." When the two counts match, add: "nothing here is beyond chance". The bar's hover gives the unit. Left-out factors sort with the rest, as 05:125 says.
5. **Tell me what to try for the factor in hand.** Changes 06:47, "[Try without] [Try v]". Spell out the list: `with a direction`, `by year`, `with a partner` (an interaction), `without`. Above it, one "Suggests" line with its evidence, for every model type and not only the GLM. The pane's figures line names the pattern in words: "too high at 0 to 2, too low at 10+".
6. **Make Apply say what it does to the other models, and show the time before a Try.** Changes 06:191, "[Apply] [Keep as a reference model] [Drop] [What changed]". New: "[Apply: leaves ncd_years out for all models. GLM and LightGBM go out of date]". Use one label, `Keep as a reference model`, in all three places. The toast after `T` reads "Trying t-boost without ncd_years, about 4 min [Cancel]". That settles 04:305-306 against 06:340.
7. **Put the cost in the hand-edit preview, and remove the contradiction.** Changes 06:156-158: "nothing else moves", "on the rows you were looking at" and "will disturb: veh_age, region". New, in place of those three lines:

        Preview. Laid on top of the fit: no other table moves now.
          rows this edit touches, 9% of exposure (validation rows, which you have seen):
            actual 0.67 . expected 0.69 -> 0.66
          cost: these rows, no clear difference (-0.2, -0.9 to +0.4) . overall, none
          0.66 is inside the spread across folds: the data cannot tell the two apart
          would move in a refit: veh_age, region . total 1.000 -> 0.998 [Rescale the base]

   This cuts `[Try this edit]` (06:155; the workflow asks for it at 04:136 and 04:304) while edits are laid on top. Apply needs no training and the edit list has undo (06:84), so Try adds only a second button. It returns when a model type can refit around an edit.
8. **Name the two homes for a typed value.** Changes 06:46, "fixed value: none". New: "business-fixed value, all models: none". The table pane's row is headed "hand edit, this model only". Both show their scale: "x rate, against the average", with a base-level switch drawn in state 5a. One sentence under the table: "make judgement edits last".
9. **A "where things stand" line on opening a node that has a logged run.** Changes 06:67, "One line, only when there is news". New: on opening, the notice line reads "Logged 12 Nov: t-boost refit, test rows 3.4. Since then: 3 settings changed, 41k new rows, 2 models out of date, 1 what-if dropped. [Details]". Details opens `What changed` against Last logged. Closed notices stay in a list under the same line. Add the model card to "Where everything lives": a `Card` link on the Last logged row.
10. **One set of words at setup, and the run pick before the first Train.** Changes 04:16-17, "say what the target is (a count, an amount, a rate or yes/no)", to the analytics draft's four: claim counts, claim sizes, pure premium, yes/no. Changes 06:184, "Next: rows (Sample 10%) (Full)*  depth (Quick)* (Thorough)", to "rows (Sample 10%) (Full)*  evaluation (Quick: validation rows)* (Thorough: 5 folds)". Show the same line in the Add list above `Add and train`, with the reason: "full rows: about 6 min for four trainings, two of them the tool's yardsticks".

**Smaller, and worth doing.** Draw the Checks pane and its readout: it is the one shared pane with no wireframe. Add `r`, `.`, `-` and `+` to the legend at 06:35-37. Show training progress on a chip as a bar, never as a bare percentage. Say "exposure and claims" on screen, not "mass and events".

## Concerns about settled items

None. One observation, inside the settled lines. Because "factors and monotonicity are shared by every model", applying a leave-out tried on one model must touch the others. Fix 6 handles this with words on the button. It needs no change to the settled line.

## Housekeeping

- I read four files: the team brief and drafts 04, 05 and 06. I opened nothing in `before/`, no other report and no code.
- I wrote this file only, and corrected it with inline edit commands that left nothing on disk. I started no agents.
