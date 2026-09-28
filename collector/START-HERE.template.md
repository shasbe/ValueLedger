# ValueLedger — a 15 minute test

Thanks for trying this. Below is what it is, then two things to test. Start with
the first — it works for anyone with a Claude account. The second needs some
Claude history on your computer, which you may or may not have.

---

## What this is

When a company uses Claude — Claude Code, Cowork, or chat — it gets one bill.
What it doesn't get is any idea of **what the money was spent on**. A finance
team sees "$40,000 of Claude last quarter" and has no way to tell whether that
was the payments project, a marketing launch, or somebody's side experiment.

ValueLedger tries to fix that. It works out what each Claude session cost and
**what it was for** — which business initiative, what kind of work — then shows
the total split up by initiative, so the spend becomes legible.

Two questions it answers that nobody can answer today:

- **Where did the money go?** Not "which person", but which piece of business.
- **What came out of it?** It counts real things — commits, merged pull
  requests — rather than asking anyone to estimate their own productivity.

You're testing against a made-up set of Neo4j initiatives. Your work almost
certainly won't match them, and that's fine — "none of these fit" is a
legitimate answer the system is designed to give, and watching it do that is
part of the test.

**This is a prototype.** Rough edges are expected; telling me about them is the
point.

---

## Part 1 — Chat  ·  start here

Have a normal conversation with Claude, then ask it to file what that
conversation was for. Claude looks back over what you did together, decides
which business initiative it belongs to, and records it in the ledger.

**You need:** a paid claude.ai plan (custom connectors aren't on the free tier).
Nothing to install, nothing to pay beyond your normal usage.

### Connect it — 5 minutes, one time

1. Go to **claude.ai → Settings → Connectors**
2. Click **Add custom connector**
3. Paste this address:

```
{{SERVICE_URL}}/mcp
```

4. Click **Connect**. A page appears asking you to authorize.
5. Both fields are already filled in for you:

| | |
|---|---|
| **Organization API key** | `{{API_KEY}}` |
| **Act as** | `{{USER_EMAIL}}` |

   Press **Authorize**.

That's a shared test account, so you may see entries from other people trying
this. If you'd rather keep yours separate, replace **Act as** with any made-up
label like `tester@example.com` before authorizing.

**Please don't put your real email there.** This is a public test site — whatever
you enter is stored and visible to anyone with the link.

### Try it

Start a new chat and do something real — draft a plan, analyse something, think
a problem through. Then say:

```
attribute this work
```

Claude will fetch the list of initiatives, decide which one this conversation
belongs to, and file it.

### What to look for

- Does it **admit when it isn't sure**? Confidence below 0.7 sends an entry for
  human review. A classifier that's always confident isn't reading properly.
- Does it **refuse to count work that wasn't finished**? If you only discussed a
  document, it shouldn't claim you produced one.
- Does it say **unclassifiable** when your conversation doesn't match any Neo4j
  initiative? That's correct, not a bug.
- It won't report a cost for chat. Deliberate — Claude can't see its own token
  usage, so the honest answer is "unknown" rather than a guess.

### Optional: add the skill

You don't need this to test — the connector already tells Claude how to use the
ledger. But the package includes **`attribute-work-skill.zip`**, which gives
Claude fuller instructions for this particular job: more careful about admitting
uncertainty, and about not counting work that wasn't actually finished.

**On claude.ai:** Settings → Capabilities → Skills → **Upload skill**, then pick
`attribute-work-skill.zip` from this folder.

**On Claude Desktop:** unzip it into `~/.claude/skills/` so you end up with
`~/.claude/skills/attribute-work/SKILL.md`, then quit Claude completely (Cmd+Q)
and reopen it.

Try it once **without** the skill first, then again with it. If the results look
much the same, that is worth knowing too.

---

## Part 2 — The collector  ·  only if you have Claude history

### What this one actually does

Part 1 worked by asking Claude to describe its own conversation. This part needs
no asking: Claude Code and Cowork **already write a record of every session to
your computer**, and the collector reads those files.

It does **not** watch you work. It reads the transcripts **Claude already saved
on your computer**, after the fact — every Claude Code and Cowork session is
written to a file in `~/.claude/projects/` as it happens. The collector opens
those files, adds up the tokens to get a real cost, and works out what each
session was for.

Two consequences:

- **If you've never used Claude Code or Cowork, there is nothing to read** and
  the tool will tell you so. Chat conversations aren't saved to your computer at
  all — which is why Part 1 has to ask Claude directly.
- **It only sees roughly the last month.** Claude deletes old transcripts after
  about 30 days, so anything older is already gone.

### Don't have any history?

Use **Claude Code** or **Cowork** for a bit first — genuinely, on anything.
Ask it to explain a repository, write a script, look at some data. Three or four
real sessions is plenty to make the output interesting. Then come back.

If you'd rather not, Part 1 alone is a perfectly good test.

**You need:** a Mac or Linux machine with some Claude Code or Cowork history,
about 10 minutes, and roughly **10 cents** of Claude API usage. You'll see the
estimate before anything is spent, and you have to say yes.

### Run it

1. Unzip this folder somewhere you can find it, like your Desktop.
2. Open **Terminal** (press Cmd+Space, type "Terminal", hit Enter).
3. Type `cd ` — with a space after it — then **drag the unzipped folder onto the
   Terminal window**. It fills in the path. Press Enter.
4. Type this and press Enter:

```
./start
```

It will tell you how many sessions it found, set itself up (about 30 seconds,
first time only), show you what it *would* do and what it would cost — spending
nothing — then ask whether to continue.

Answer **n** and you've still seen the session list and the estimate, which is
most of the interest. Nothing is sent.

### Filing it under your own name

By default this reports as **{{USER_EMAIL}}**, the same shared test account as
Part 1. To keep your entries separate, pass any label you like:

```
./start --email tester@example.com
```

It does not have to be a real address and it does not need setting up in
advance — whatever you pass is registered on first use. **Don't use your real
email**: this is a public test site and whatever you enter is visible to anyone
with the link.

Use the same label in Part 1's **Act as** field and both halves show up
together.

### What to look for

- Does the session count look roughly right for how much you've used Claude?
- Does the classification look sensible? Work that genuinely doesn't fit any
  Neo4j initiative should come back **unclassifiable** rather than being forced
  into the nearest one.
- Anything obviously wrong — that's the most useful feedback of all.

---

## Seeing the results

Open this in a browser:

```
{{SERVICE_URL}}
```

Two things worth a look:

- **The CFO view** (the button at the top) — the finance summary: where the
  money went, what came out of it, and one number explicitly labelled as an
  estimate rather than a measurement.
- **Explore the data yourself** — pick a role and the numbers genuinely change.
  A "developer" sees only their own sessions; finance sees everything. That's
  enforced on the server, not hidden in the page.

Everything you file appears under **{{USER_EMAIL}}** unless you chose your own
label. That account is shared with anyone else testing this, so you may see
entries that aren't yours — switch the email in the header to your own label to
see just yours.

---

## What leaves your computer

**Your prompts never do.** Sessions are read and classified *on your machine*;
only the conclusion is sent.

The collector is built in **strict** mode, which also withholds:

- repository and folder names — replaced with a code like `r-6c457c2d`
- branch names, commit hashes, file counts
- the sentence the classifier wrote explaining its choice

What *is* sent: how many tokens a session used, what that cost, which initiative
and kind of work it was, and how confident the classifier was.

Worth knowing: the results page is **public to anyone with the link**. Even in
strict mode, the pattern of your work — when, how much, roughly what kind — is
visible there. If that's not okay for your situation, do Part 1 only, or answer
**n** in Part 2.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| No "Add custom connector" on claude.ai | Custom connectors need a paid plan |
| `No Claude sessions found` | Nothing for the collector to read — use Claude Code or Cowork first, or just do Part 1 |
| `Python 3.10 or newer is needed` | Install it from python.org, then run `./start` again |
| `permission denied` | Run `chmod +x start` first, then `./start` |
| Something else | Copy what you saw and send it over — the message is enough to work from |
