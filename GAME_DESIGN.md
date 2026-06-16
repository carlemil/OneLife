# OneLife — Game Design Document

> Working notes, translated and organized. Original notes were a mix of English and Swedish; all Swedish has been translated to English and grouped by topic. This is a living document for early design — nothing here is final.

---

## 1. Concept / Vision

A **web-based, browser-playable text adventure** presented as a continuous **stream of text** — your interactions with various **agents** (NPCs, locations, and possibly other agent types). It borrows from classic adventure games like **Déjà Vu, Zork, Monkey Island, and Loom**.

The world is **slightly dark and definitely dangerous** — a place where a player can get stuck and end up dead. **Rollback is always an available escape path**, but the cost is very real: rolling back drops your position on the leaderboard.

The ambition is a **jungle of story arcs** — rich in characters and locations, a world at huge scale in terms of possible interactions, that still follows a coherent timeline and stays consistent across other players' sessions. The stories of other players' interactions may **spread into your game**.

---

## 2. Core Gameplay Loop

- A **story tree** to progress through.
- **Very low risk of failure at each individual step.**
- **Static (authored) story between steps**, with **many possible choices at each step**.
- In a few steps, the **AI is prompted** and the player must **talk their way through** it.
  - *Open question: is this feasible?* (See §11.)
- The player has **no inventory**. Instead, the game keeps a **log of everything that happens**.
  - The **log IS your progress** in the game.
  - You can always **roll back** to an earlier point by discarding the log *x* steps backward — but of course this also rolls your **progress** back to that point, **costing leaderboard positions**.

---

## 3. Story & Setting

- **Opening:** Begin as if in *Déjà Vu* — inside **Killebäck School (Killebäckskolan)**, **Södra Sandby, Sweden**. The player is **confused**, unsure how they got there or what they're supposed to do.
- Build a **jungle of story arcs**, rich in characters and locations.
- Weave **story puzzle elements** into the game in the style of *The Fool's Errand* and similar puzzle games.
- **Embed hints to puzzles** within the text and the characters' dialogue.

---

## 4. World Structure

- A **grid** over parts of the world: a few **large cities** and some **wilderness**, starting at **Killebäckskolan, Södra Sandby, Sweden**.
- Two map levels: a **world map** and a **city map**.
- **Represent smaller towns well**, and let larger ones have several locations.
  - Scaling factor: a big city of ~10M inhabitants ≈ **~100 locations**; a small village like Sandby ≈ **~10 locations**.
- Each **location** has:
  - A few **interactions** — *what* and *who* is present there.
  - An **image** that sets the mood.
  - **Music and sound effects** that contribute to the atmosphere.
  - Sound and music can be **reused** across other, similar locations.

---

## 5. Agents (NPCs, Locations, possibly more)

The whole game is presented as your interactions with **agents**. Agent types include NPCs and locations — and possibly more.

### Memory system
- **NPCs have memories** of what has been said — and possibly of the player's actions in some way.
- Create a **memory store for agents**, and let them **access parts of each other's memories**.
- When agents share memory, generate **believable stories** to explain *how* they know those shared bits — while adding new interactions to the world.
  - These shared memories carry **locations and times** attached to them, so they **fit into the player's story progression**.

### Cross-player leakage
- Conversations with characters are **saved** so they can **leak to other characters** and **influence the story forward**.
- Because of this, **other players' stories can spread into your game** (see §1).

---

## 6. Onboarding

- On first play, **force the user to read a manual** explaining how the game works and how to play it.
- Run a **short cross-examination / quiz** afterward to make sure the player has at least grasped the basics.

---

## 7. Progression & Leaderboard

- **Progress points** are awarded for all actions, items, locations, and dialogue outcomes.
- A **leaderboard** ranks players by who has the most **"Progress."**
- **Rollback cost:** discarding log steps to escape a bad situation also subtracts progress, **dropping you on the leaderboard** — making rollback a real, meaningful cost rather than a free undo.
- **In-game popups** periodically force the player to look at the leaderboard, so it stays in focus and acts as a **driving motivator**.

---

## 8. Audio / Visual

- **Generate an image** plus **sound and/or music** themed for each location.
- Reuse audio across similar locations to save on generation cost (see §4).

---

## 9. Technical Architecture

- **Web-based**, played in the browser.
- **Two-factor login.**
- **Backend in a Docker container**, easy to set up.
- **Database** stores:
  - Locations
  - Characters
  - **Conversations** with characters (so they can leak to other characters and influence the story forward — see §5).

---

## 10. Inspirations

- *Déjà Vu* (confused-amnesiac opening, point-and-explore feel)
- *Zork* (text adventure depth)
- *Monkey Island* (character, humor, world)
- *Loom* (atmosphere, systemic puzzle design)
- *The Fool's Errand* and similar **puzzle games** (woven story puzzles + embedded hints)

---

## 11. Open Questions / To Resolve

- **AI-driven dialogue feasibility:** Can we reliably prompt the AI so that certain steps *require* the player to "talk their way through"? How do we bound this so it stays low-risk-of-failure and doesn't break the authored story tree?
- How do **cross-player memory leaks** stay coherent and not contradict each player's private timeline?
- How is the **global timeline** kept consistent across many concurrent sessions?
- What exactly counts as an **agent** beyond NPCs and locations ("possibly more")?
- Balance of **static authored story** vs. **AI-generated** content per step.
- Cost model for **per-location image/audio generation** at world scale.
