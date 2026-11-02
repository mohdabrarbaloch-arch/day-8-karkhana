# Usage

A walk through the app the way you'd actually use it: look at the cluster, submit
work, run a pass, read the bill.

## 1. Sign in

Open the app. The demo credentials are pre-filled if the database was seeded.
You land on **Cluster**.

## 2. Read the cluster

The four cards at the top are the numbers a capacity meeting argues about:

- **Utilisation** — held GPUs ÷ total GPUs. On a fresh seed that reads 14.3%
  (4 of 28 held), which is the cluster mostly idle and a fair thing to ask about.
- **GPUs held** — e.g. `4 / 28`.
- **Queue** — how many jobs are waiting.
- **Spend today** — chargeback accrued since midnight UTC. On a fresh seed this
  is `$0.00`: the seeded history ended days ago, so nothing has been billed today.

Below that, each node shows its name, GPU model, health, and a bar per GPU:
filled means held, empty means free, and a greyed row means the node is offline.

The **Scheduler** panel lists the queue in the order the scheduler will consider
it, each row carrying the reason it is still waiting.

## 3. Submit a job

Go to **Jobs**. Fill in:

- **Name** — anything you'll recognise later.
- **GPUs** — how many the job needs.
- **Model** — `any`, or a specific one like `H100-80GB`. Choosing a specific
  model narrows placement to nodes that have it.
- **Span** — `node` means all GPUs must be on one machine (a gang request).
  `any` lets them spread.
- **Priority** — 1 to 10. Higher goes first, with ageing added on top.
- **Estimated minutes** — used for the reservation window. The field is
  labelled **Est. minutes** in the form; the API calls it `duration_minutes`.

Click **Queue job**. It lands in `queued` and appears in the scheduler panel with
its reason.

Try this to see the rules bite: submit a job asking for 16 GPUs of `H100-80GB`
with span `node` on a cluster whose largest node has 8. It will be accepted and
reported as **cannot fit**, with the reason naming the shortfall. Submitting
it succeeds because queueing a job and placing it are different questions.

Note the job's `status` stays `queued`. "Unplaceable" is a scheduling verdict
recorded in `last_reason` (and in the pass result), not a status of its own —
so the job is still visible in the queue, and will place if hardware changes.

## 4. Run a scheduling pass

Back on **Cluster**, click **Run pass**. The result panel lists what happened,
one line per job:

```
started    Job #8 → a100-01 (2 GPU, backfill)
waiting    Job #6: head of queue; needs 24 GPU(s), next capacity window in 176 min
cannot fit Job #5: 16 GPU(s) of H100-80GB on a single node exceeds every node in the cluster
```

Three outcomes, plus a fourth line when the head of the queue holds a
reservation — the panel prints the pass `message` as its heading first:

- **started** — it placed now, on the named node. The reason says whether it
  went in as the head of the queue or as a **backfill** that squeezed in ahead
  of the reservation because it will finish before the head needs the GPUs.
- **waiting** — it will run, later. The reason gives the wait in minutes.
- **cannot fit** — it will never run on this cluster as configured. Different
  reason strings point at different fixes (too many GPUs for any one node, or
  the only matching node is unhealthy).
- **reserved** — appended when `reservation_minutes` is set, stating how long
  the head job is being held capacity for.

Those are exactly the three arrays in the API response (`scheduled`,
`deferred`, `unplaceable`) plus the `reservation_minutes`/`head_job_id`
summary.

## 5. Finish or cancel a job

In the **Jobs** table, a running job has **Finish**; a queued or running one has
**Cancel**.

**Finish** calls `/api/jobs/{id}/complete?exit_code=0` — the UI reports success
only, with exit code `0`. To record a failure you call the endpoint yourself
with a non-zero code. Failing is a real outcome, not an error state, so it still
gets billed for the time it used.

**Cancel** is a separate button. Cancelling a queued job frees nothing (it held
nothing) but clears the queue; cancelling a running one releases its GPUs.
Cancelled jobs are still billed for the minutes they actually ran.

## 6. Read the bill

**Billing** shows GPU-hours and cost per team for a window.

```
cost = gpus × (billed_minutes ÷ 60) × rate_per_gpu_hour
```

Billed minutes are the job's whole elapsed minutes, with a **floor of one
minute**: a 40-second job costs a minute, but a 61-second job also costs one
minute, not two. The table lists GPU-hours and cost; the footer totals them. The
arithmetic is in `app/engine/billing.py` if you want to check a row by hand —
do check, that's the point.

Billed statuses are `completed`, `failed` and `cancelled`. Running jobs are
excluded because their minutes aren't final yet.

## Roles

| Role | Can do |
|---|---|
| `operator` | Submit and cancel jobs; read the cluster. Sees only their own team's spend |
| `admin` | Everything, plus add nodes, set node health, drain nodes, delete jobs, and see every team's spend |

The first account registered on an empty database becomes `admin`; every later
registration is `operator`. There is no `viewer` role — the two that exist are
the ones the authorisation checks actually enforce.

Nodes are admin-only because adding capacity is a capacity decision, not a
scheduling one.
