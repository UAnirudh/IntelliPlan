# Streak and Reward System Design

This document defines the Study & Learn retention economy. The goal is to move from a passive points counter to a daily habit loop with streak protection, repair, spendable currency, milestones, levels, badges, quests, and cosmetics.

## 1. Current System Evaluation

Common weaknesses in basic streak systems:

- Streaks are only a number, so they stop feeling meaningful after the first few days.
- Points only accumulate, so users never have an active goal after earning them.
- One missed day feels punitive and can cause users to quit instead of return.
- Milestones are too small to feel celebratory.
- There is no weekly structure, so motivation resets only when the user remembers to come back.
- Users do not build identity through titles, cosmetics, badges, or visible progress.

| Dimension | Current System | Redesigned System |
| --- | --- | --- |
| Currency | Points accumulate, never spent | Sparks are earned and spent in a shop |
| Streak protection | 2 freezes, earned rarely | Tiered freeze capacity with weekly passive accrual at 30+ days |
| Streak recovery | Missed day can feel like full reset | 48-hour repair window with Spark cost and optional Repair Token discount |
| Milestone rewards | Small point bumps | 25-2,000 Sparks, freezes, badges, and titles |
| Shop | Does not exist | 20 items across protection, boosters, inventory, and cosmetics |
| Short-term goals | None | 3 weekly quests refresh every Monday |
| Progression | Streak count only | Level 1-50, 30 badges, titles, and cosmetic frames |
| Motivation feedback | Static streak number | Tier names, warnings, celebrations, calendar, and reward overlays |
| Long-term retention | Falls off after early streak novelty | Escalating bonuses and passive perks reward staying for months |
| User identity | None | Badges, titles, streak frames, and colors create ownership |

## 2. Streak System

Daily rules:

- A streak day requires completing at least 5 study questions.
- Completing the first qualifying session of the day increments or starts the streak.
- Same-day repeat sessions earn normal Sparks but do not increment the streak again.
- Each streak tier grants an automatic daily bonus.
- The app shows urgency warnings after 6 PM if the streak is not protected for the day.

Protection and repair:

- Streak Freezes automatically protect missed days when available.
- Freeze capacity scales by streak tier.
- Users at 30+ days earn 1 passive weekly freeze; users at 100+ days earn 2.
- If no freeze is available, the user receives a 48-hour repair window.
- Streak Repair costs Sparks, can be used once every 30 days, and unlocks the Comeback Kid badge.
- Repair Tokens cut the next repair cost in half.

| Streak Tier | Days | Daily Bonus | Freeze Capacity | Primary Hook |
| --- | ---: | ---: | ---: | --- |
| Spark | 1-6 | 5 Sparks | 2 | Easy early wins |
| Flame | 7-13 | 10 Sparks | 3 | First-week identity |
| Blaze | 14-20 | 15 Sparks | 3 | Two-week momentum |
| Inferno | 21-29 | 20 Sparks | 5 | Loss aversion starts to matter |
| Wildfire | 30-59 | 30 Sparks | 5 | Passive weekly protection begins |
| Firestorm | 60-99 | 50 Sparks | 5 | Monthly commitment payoff |
| Legendary | 100-364 | 75 Sparks | 5 | Long-term prestige |
| Eternal | 365+ | 150 Sparks | 5 | Year-long status |

## 3. Currency: Sparks

Sparks are the main in-app currency.

Users earn Sparks from:

- Answering questions and completing sessions.
- Daily streak tier bonuses.
- Perfect sessions.
- Serious or Extreme study modes.
- Weekly quests.
- Streak milestones.
- Level-up bonuses.

| Reward Source | Sparks |
| --- | ---: |
| First session today | 10 |
| Session complete | 20 |
| Serious mode bonus | 10 plus active-time Sparks |
| Extreme mode bonus | 25 |
| Perfect session | 30 |
| Daily streak bonus | 5-150 by tier |
| Weekly quest | 50-120 |
| Weekly quest set bonus | 150 plus 1 freeze |
| Level up | 50 |

| Milestone | Sparks | Freezes | Badge or Title |
| ---: | ---: | ---: | --- |
| 3 days | 25 | 0 | First Flame |
| 7 days | 75 | 1 | Week Warrior |
| 14 days | 100 | 1 | Fortnight Fighter |
| 21 days | 150 | 0 | Inferno Initiate, Grinder title |
| 30 days | 250 | 2 | Monthly Master title |
| 60 days | 500 | 2 | Sixty Strong |
| 100 days | 1,000 | 3 | Century Club, Legendary title |
| 365 days | 2,000 | 3 | Year of Fire, Eternal title |

## 4. Spark Shop

| Item | Price | Type | Effect |
| --- | ---: | --- | --- |
| Streak Freeze | 200 | Protection | Blocks one missed day |
| Freeze Pack | 500 | Protection | Adds 3 freezes |
| Weekend Shield | 350 | Protection | Adds 2 freezes for busy weekends or travel |
| Repair Token | 900 | Retention | Cuts next streak repair cost in half |
| 2x Sparks | 100 | Booster | Doubles Sparks in the next session |
| 3x Sparks | 250 | Booster | Triples Sparks in the next session |
| Daily Booster | 350 | Booster | +50% Sparks for 24 hours |
| Focus Booster | 180 | Booster | +25% Sparks for 72 hours |
| Question Skip Pack | 75 | Inventory | Adds 5 skips |
| Hint Token Pack | 120 | Inventory | Adds 10 hints |
| Streak Color: Gold | 400 | Cosmetic | Gold streak glow |
| Streak Color: Neon | 400 | Cosmetic | Neon streak glow |
| Streak Color: Forest | 400 | Cosmetic | Green streak glow |
| Profile Title: Scholar | 300 | Cosmetic | Displays Scholar title |
| Profile Title: Grinder | 300 | Cosmetic | Displays Grinder title |
| Profile Title: Comeback Kid | 450 | Cosmetic | Displays Comeback Kid title |
| Calendar Theme: Dark | 500 | Cosmetic | Dark calendar style |
| Streak Frame: Ember | 600 | Cosmetic | Ember streak frame |
| Streak Frame: Aurora | 800 | Cosmetic | Aurora streak frame |
| Streak Frame: Cosmic | 950 | Cosmetic | Premium cosmic frame |

## 5. Progression and Rewards

Leveling:

- Users progress from Level 1 to Level 50 based on lifetime Sparks earned.
- Level titles are Learner, Student, Scholar, Researcher, Expert, Veteran, Mentor, Master, and Legend.
- Each level-up gives 50 bonus Sparks and a celebration overlay.

Badges:

- Streak badges: First Flame, Week Warrior, Fortnight Fighter, Inferno Initiate, Monthly Master, Sixty Strong, Century Club, Year of Fire.
- Session badges: First Session, Getting Serious, Dedicated, Committed, Unstoppable.
- Accuracy badges: Sharp, Precise, Flawless.
- Special badges: Perfect Week, Speed Demon, Night Owl, Early Bird, Comeback Kid.
- Quest and shop badges: Quest Starter, Quest Finisher, Weekly Champion, Spark Shopper, Deal Hunter, Freeze Ready, Booster Pilot, Spark Saver, Style Setter.

Weekly quests:

- 3 quests refresh every Monday.
- Quest examples include studying 5 days, answering 75 questions correctly, completing 3 Serious or Extreme sessions, maintaining the streak all 7 days, mastering 3 concepts, earning 200 Sparks in one session, completing a perfect session, or studying 30+ minutes.
- Completing all 3 quests gives 150 bonus Sparks and 1 freeze.

Optional social extensions:

- Weekly friends leaderboard by Sparks earned.
- Private study groups with shared quest goals.
- Streak reaction messages when friends hit milestones.
- Non-punitive leagues that reward consistency more than raw time spent.

## 6. Retention Mechanics

- Habit loop: cue is the daily streak warning, routine is a 5-question session, reward is Sparks, streak progress, and celebration feedback.
- Loss aversion: streak freezes, repair windows, and urgent banners make the existing streak feel worth protecting.
- Variable rewards: weekly deals, randomized weekly quests, milestone celebrations, badges, and boosters prevent the economy from feeling flat.
- Long-term progression: level 1-50, tier names, badges, and passive freeze perks give months of goals.
- Micro-goals: daily streak, 5-question minimum, weekly quests, next milestone, and next level keep the next action small.
- Identity: titles, frames, colors, badges, and streak tier names help users feel like the app reflects their effort.

## 7. Final Summary

The redesigned system turns streaks into a complete reward ecosystem. Users now have a daily reason to return, a currency worth earning, meaningful ways to spend it, protection from discouraging streak loss, milestones that feel valuable, and long-term identity through levels, badges, titles, and cosmetics.
