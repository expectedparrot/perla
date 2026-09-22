# Starbucks live EDSL example

The main recorded run is `dated-live`: three calendar rounds (January–March 2027),
30 accepted responses, and two rejected/incomplete rebuttals retained with their
retries. See `dated-live/execution/audit.json` for remote job ids, Results hashes,
model identities and the six unassessed judgment rulings. Observed metrics remain
missing. No human assessment or final endgame evaluation was performed.

`live` preserves the earlier engineering run, including citation/branch failures
and a round-date ambiguity. The current exporter names the current round and
scheduled date explicitly. The guide includes an actual contested ruling from
that archive separately from the main dated transcripts.

To play a new game, follow the [guide](../../docs/index.html): initialize with
`perla init`, load the full cast with `perla actor load --input case/players.json`, then export
named job batches, execute them with the ep CLI, and import the Results:

```sh
perla job generate moves --move 1 --output round-1/moves
ep run --jobs round-1/moves/home.jobs.ep --model_list models/home.ep \
  --background --wait --timeout 900 --remote_inference_results_visibility private \
  --fresh --output round-1/moves/home.results.ep
# Execute the other three actor files as shown in the guide, then:
perla ingest moves --from round-1/moves
```

The tutorial and its execution reference include setup, reusable `ep models create` configurations and the
complete command sequence for all three rounds. Run from a fresh game directory;
no Python execution runner or JSON response parsing is needed. Perla remains
responsible for game state and validation; ep handles inference and remote jobs.

`run_live.py` is retained as a historical record of how the original transcripts
were executed. Its direct remote-API calls are not the recommended workflow.
The archived transcripts have not been relabeled as ep CLI executions.

Source case: `docs/case/starbucks-queue`. Narrative source: `docs/guide.html` and
`docs/round-notes`. Build `docs/index.html` using `python scripts/build_docs.py`;
this does not execute inference. It reads the checked-in dated run and copies its
supporting artifacts into the guide's download directory.

`cli-live` records the fresh verification through the actual ep CLI and named
Perla exports/imports. Its `audit.json` links each command and remote receipt to
the accepted Results. These new responses verify the workflow and remain separate
from the original dated case analyzed in the tutorial.
