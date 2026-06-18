<script>
  import { Handle, Position } from '@xyflow/svelte';
  // data = { ntype, rec } where rec is the full authored node.
  let { data, selected } = $props();
</script>

<div class="storynode {data.ntype}" class:sel={selected}>
  <Handle type="target" position={Position.Top} />
  <div class="nt">{data.ntype}{#if data.rec.entry} · entry{/if}</div>
  <div class="nid">{data.rec.id}</div>
  {#if data.rec.title}<div class="ntitle">{data.rec.title}</div>{/if}
  <Handle type="source" position={Position.Bottom} />
</div>

<style>
  .storynode {
    width: 150px; padding: .4rem .55rem; border-radius: 8px;
    border: 1px solid #3a456a; background: #2a3550; color: #e8e8f0;
    font-family: Georgia, serif; font-size: .8rem; box-shadow: 0 1px 4px rgba(0,0,0,.4);
  }
  .storynode.narration { background:#2a3550; }
  .storynode.choice    { background:#2c2f4a; }
  .storynode.gate      { background:#3a2f4a; border-color:#6a4a8a; }
  .storynode.puzzle    { background:#2a3a2f; border-color:#4a6a3a; }
  .storynode.location  { background:#2a2620; border-color:#6a5a3a; }
  .storynode.death     { background:#3a2330; border-color:#6a3346; }
  .storynode.ending    { background:#23323a; border-color:#356a5a; }
  .storynode.sel { outline: 2px solid #cdbb9a; }
  .nt { font-size:.62rem; text-transform:uppercase; letter-spacing:.04em; color:#9aa; }
  .nid { font-weight:bold; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ntitle { color:#cdbb9a; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
</style>
