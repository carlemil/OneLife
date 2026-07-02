<script>
  // Crossword grid + clue lists. The player answers one clue at a time (the word
  // input lives in App.svelte); this component renders the grid the server sends
  // (black/white cells, start-numbers, revealed letters) and the Across/Down clue
  // lists, and reports the selected clue back via `onselect`. It never sees answers —
  // only the letters the server has revealed from solved entries (interlocks).
  let { data = null, selected = '', onselect = () => {} } = $props();

  const entries = $derived(data?.entries ?? []);
  const across = $derived(entries.filter((e) => e.dir === 'across'));
  const down = $derived(entries.filter((e) => e.dir === 'down'));
  const letters = $derived(data?.letters ?? []);
  const sel = $derived(entries.find((e) => e.id === selected) || null);

  function cellsOf(e) {
    const out = [];
    for (let i = 0; i < e.len; i++) {
      out.push(e.dir === 'down' ? `${e.row + i},${e.col}` : `${e.row},${e.col + i}`);
    }
    return out;
  }

  // Cells covered by the selected entry, for the highlight.
  const selCells = $derived(new Set(sel ? cellsOf(sel) : []));

  // Entries covering a cell — click selects; clicking a cell already in the selected
  // entry toggles to the crossing word (across ⇄ down).
  function entryAt(r, c) {
    const covering = entries.filter((e) => cellsOf(e).includes(`${r},${c}`));
    if (!covering.length) return null;
    if (sel && covering.length > 1 && covering.some((e) => e.id === sel.id)) {
      const other = covering.find((e) => e.id !== sel.id);
      if (other) return other;
    }
    return covering[0];
  }

  function clickCell(cell) {
    if (cell.black) return;
    const e = entryAt(cell.row, cell.col);
    if (e) onselect(e.id);
  }
</script>

{#if data}
  <div class="xword">
    <div class="grid" style={`grid-template-columns:repeat(${data.cols},1fr);max-width:${data.cols * 2.1}rem`}>
      {#each data.cells as cell}
        {#if cell.black}
          <div class="cell black"></div>
        {:else}
          <button type="button" class="cell" class:sel={selCells.has(`${cell.row},${cell.col}`)}
                  onclick={() => clickCell(cell)}>
            {#if cell.number}<span class="num">{cell.number}</span>{/if}
            <span class="ltr">{letters[cell.row]?.[cell.col] ?? ''}</span>
          </button>
        {/if}
      {/each}
    </div>

    <div class="clues">
      {#each [['Across', across], ['Down', down]] as [heading, list]}
        <div class="cluelist">
          <h4>{heading}</h4>
          {#each list as e}
            <button type="button" class="clue" class:sel={e.id === selected} class:done={e.filled}
                    onclick={() => onselect(e.id)}>
              <b>{e.number}</b> <span class="ct">{e.clue}</span>{#if e.filled} ✓{/if}
            </button>
          {/each}
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .xword { margin:.4rem 0 .6rem; }
  .grid { display:grid; gap:2px; margin:0 0 .6rem; }
  .cell { position:relative; aspect-ratio:1; padding:0; border:1px solid #4a4033;
    background:#efe6d0; color:#2a2118; font:700 clamp(.7rem,3.6vw,1.1rem)/1 Georgia, serif;
    text-transform:uppercase; display:flex; align-items:center; justify-content:center;
    cursor:pointer; }
  .cell.black { background:#241f18; border-color:#241f18; cursor:default; }
  .cell.sel { background:#f6d98c; box-shadow:inset 0 0 0 2px #c0563a; }
  .cell .num { position:absolute; top:1px; left:2px; font:400 .5rem/1 Georgia, serif;
    color:#6a5a44; }
  .cell .ltr { pointer-events:none; }
  .clues { display:flex; gap:1rem; flex-wrap:wrap; }
  .cluelist { flex:1 1 12rem; min-width:11rem; }
  .cluelist h4 { margin:.2rem 0 .3rem; color:#cdbb9a; font:600 .85rem Georgia, serif;
    text-transform:uppercase; letter-spacing:.04em; }
  .clue { display:block; width:100%; text-align:left; background:none; border:none;
    padding:.2rem .3rem; margin:0; color:#d8d2c4; font:400 .9rem Georgia, serif;
    cursor:pointer; border-radius:4px; }
  .clue:hover { background:rgba(255,255,255,.05); }
  .clue.sel { background:rgba(192,86,58,.22); color:#fff2e8; }
  .clue.done { color:#8fae86; }
  .clue b { color:#cdbb9a; margin-right:.15rem; }
  .clue.done .ct { text-decoration:line-through; opacity:.8; }
</style>
