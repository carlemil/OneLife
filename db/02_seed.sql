-- OneLife vertical slice: the opening at Killebäckskolan.
-- Mirrors the worked example in STORY_AND_PUZZLES.md §8.

INSERT INTO locations (id, name, description) VALUES
  ('killebackskolan', 'Killebäckskolan', 'A school in Södra Sandby. It should be empty at this hour.');

INSERT INTO characters (id, name, persona) VALUES
  ('the-janitor', 'The Janitor',
   'A wary old caretaker. Speaks in short, clipped sentences. Suspicious of strangers, but softens if treated with kindness or pity. He has worked here for decades and knows the building''s secrets. He does NOT know anything about the world outside Sandby or the larger mystery, and deflects such questions.');

INSERT INTO story_arcs (id, title, is_spine) VALUES
  ('main', 'The Killebäck Awakening', TRUE);

-- ---------- Nodes ----------
INSERT INTO story_nodes (id, arc_id, type, location_id, title, body, is_entry, is_death, gate_id, puzzle_id, media) VALUES
  ('kbk-wake', 'main', 'narration', 'killebackskolan', 'Déjà Vu',
   'You come to with a jolt. Fluorescent light hums overhead. The smell of floor polish and old paper. You are sitting on the cold tiles of a school you are certain you have stood in before — Killebäckskolan — though you cannot say when, or how you got here tonight. Rain ticks against the high windows. You should not be here.',
   TRUE, FALSE, NULL, NULL, '{"image_theme":"school-hallway-dusk","music_theme":"uneasy-quiet"}'),

  ('kbk-entrance-hall', 'main', 'location', 'killebackskolan', 'The Entrance Hall',
   'The entrance hall stretches before you, lined with dented lockers. A mop bucket sits abandoned beside a heavy door marked PANNRUM. By the lockers, an old janitor works a rag over a brass plaque, pretending not to watch you.',
   FALSE, FALSE, NULL, NULL, '{"image_theme":"school-hallway-dusk","music_theme":"uneasy-quiet"}'),

  ('kbk-janitor-gate', 'main', 'gate', 'killebackskolan', 'The Janitor',
   'The old man keeps his eyes on the brass. "School''s closed," he mutters. "You shouldn''t be here." (Talk to him — try to find out how to get out.)',
   FALSE, FALSE, 'kbk-janitor-find-the-exit', NULL, '{"image_theme":"school-hallway-dusk","music_theme":"uneasy-quiet"}'),

  ('kbk-boiler-room', 'main', 'puzzle', 'killebackskolan', 'The Boiler Room',
   'The boiler room is hot and close, pipes groaning in the dark. Against the far wall stands a rusted steel cabinet, secured with a 4-digit padlock.',
   FALSE, FALSE, NULL, 'boiler-cabinet-code', '{"image_theme":"boiler-room-dark","music_theme":"low-dread"}'),

  ('kbk-the-drawing', 'main', 'ending', 'killebackskolan', 'The Drawing',
   'The cabinet swings open. Inside, taped to the back panel, is a child''s crayon drawing: a stick figure outside this very school, under a scrawled date. Your hands are shaking, because you recognize the drawing. You drew it. ▒ End of the prototype slice. ▒',
   FALSE, FALSE, NULL, NULL, '{"image_theme":"boiler-room-dark","music_theme":"revelation"}'),

  ('kbk-caught', 'main', 'death', 'killebackskolan', 'In the Dark',
   'You throw your shoulder against the PANNRUM door. It gives with a shriek of metal — into pure black. Something in that dark had been waiting a very long time. It does not hesitate. ▒ You have died. ▒',
   FALSE, TRUE, NULL, NULL, '{"image_theme":"boiler-room-dark","music_theme":"low-dread"}');

-- ---------- Edges ----------
INSERT INTO story_edges (id, from_node, to_node, label, conditions, effects, danger, sort_order) VALUES
  ('e-wake-bearings', 'kbk-wake', 'kbk-entrance-hall',
   'Get your bearings', '{"all":[]}', '{"progress_points":5,"log":"You got to your feet in the entrance hall."}', 0, 0),

  ('e-hall-janitor', 'kbk-entrance-hall', 'kbk-janitor-gate',
   'Approach the old janitor', '{"all":[]}', '{"progress_points":5,"log":"You approached the janitor."}', 0, 0),

  ('e-hall-plaque', 'kbk-entrance-hall', 'kbk-entrance-hall',
   'Examine the brass plaque the janitor is polishing',
   '{"all":[{"not":{"flag_set":"examined_plaque"}}]}',
   '{"progress_points":10,"set_flag":"examined_plaque","log":"You read the memorial plaque: \"IN MEMORIAM — 1998\"."}', 0, 1),

  ('e-hall-pannrum', 'kbk-entrance-hall', 'kbk-boiler-room',
   'Open the PANNRUM (boiler room) door',
   '{"all":[{"flag_set":"knows_boiler_exit"}]}',
   '{"progress_points":15,"log":"You slipped through the boiler-room door."}', 0, 2),

  ('e-hall-force', 'kbk-entrance-hall', 'kbk-caught',
   'Force the PANNRUM door open',
   '{"all":[{"not":{"flag_set":"knows_boiler_exit"}}]}',
   '{"log":"You forced the door."}', 2, 3),

  ('e-gate-leave', 'kbk-janitor-gate', 'kbk-entrance-hall',
   'Step back into the hall', '{"all":[]}', '{"log":"You stepped back from the janitor."}', 0, 9),

  ('e-boiler-solved', 'kbk-boiler-room', 'kbk-the-drawing',
   'Look inside the open cabinet',
   '{"all":[{"puzzle_solved":"boiler-cabinet-code"}]}',
   '{"progress_points":25,"log":"You opened the cabinet."}', 0, 0),

  ('e-boiler-leave', 'kbk-boiler-room', 'kbk-entrance-hall',
   'Go back to the hall', '{"all":[]}', '{"log":"You returned to the hall."}', 0, 9);

-- ---------- Dialogue gate (see AI_DIALOGUE_GATES.md §3) ----------
INSERT INTO dialogue_gates (id, location_id, character_id, spec) VALUES
  ('kbk-janitor-find-the-exit', 'killebackskolan', 'the-janitor', '{
    "intent": "Get the janitor to reveal how to leave the locked school.",
    "criteria": [
      {"id":"established_trust","desc":"Player is polite or not hostile, OR mentions they are lost, scared, or confused."},
      {"id":"asked_about_exit","desc":"Player asks, directly or indirectly, how to get out, where a door is, or how to leave."}
    ],
    "success_rule": "established_trust AND asked_about_exit",
    "knowledge_boundary": {
      "knows": ["the boiler-room (PANNRUM) door has been broken since 1998 and is the only way out after dark", "the newcomer looks like someone he saw here years ago"],
      "refuses": ["anything about the world outside Sandby", "the larger mystery of why the player is here"],
      "tone": "wary old man, short sentences, warms up if treated kindly"
    },
    "hint_ladder": [
      "He grumbles and keeps polishing, volunteering nothing.",
      "He glances, just for a second, toward the door marked PANNRUM.",
      "He sighs. \"That boiler-room door''s been broke since ''98. Only way out after dark. Now leave me be.\""
    ],
    "mercy_after_attempts": 6,
    "on_success": {
      "progress_points": 40,
      "set_flag": "knows_boiler_exit",
      "story_node_next": "kbk-entrance-hall",
      "log": "The janitor let slip that the boiler-room door is the way out after dark.",
      "write_memory": {"owner":"the-janitor","content":"Told the newcomer about the boiler-room exit.","leakable":true}
    }
  }');

-- ---------- Puzzle + woven clues (STORY_AND_PUZZLES.md §5) ----------
INSERT INTO puzzles (id, type, prompt, solution, required_clues, hint_ladder, on_solve) VALUES
  ('boiler-cabinet-code', 'combination',
   'The rusted cabinet has a 4-digit padlock.',
   '{"kind":"exact","value":"1998"}',
   ARRAY['year-1998-graffiti','janitor-said-98','plaque-1998'],
   '["The number feels like a year.","The janitor, the graffiti, and the memorial plaque all circle the same year.","Try 1998."]',
   '{"progress_points":60,"set_flag":"opened_boiler_cabinet","log":"The padlock clicks open."}');

INSERT INTO puzzle_clues (id, puzzle_id, placement, reveal_text, discover_conditions) VALUES
  ('year-1998-graffiti', 'boiler-cabinet-code',
   '{"kind":"location_text","location_id":"killebackskolan"}',
   'Scratched into a locker door: "DET HÄNDE HÄR ''98" (it happened here ''98).',
   '{"all":[{"node_visited":"kbk-entrance-hall"}]}'),

  ('janitor-said-98', 'boiler-cabinet-code',
   '{"kind":"dialogue","character_id":"the-janitor"}',
   'The janitor said the boiler door has been broken since ''98.',
   '{"all":[{"gate_passed":"kbk-janitor-find-the-exit"}]}'),

  ('plaque-1998', 'boiler-cabinet-code',
   '{"kind":"interaction","location_id":"killebackskolan"}',
   'The brass memorial plaque reads: IN MEMORIAM — 1998.',
   '{"all":[{"flag_set":"examined_plaque"}]}');

-- ---------- Second NPC: Märta (lights up cross-character leakage) ----------
INSERT INTO characters (id, name, persona) VALUES
  ('marta', 'Märta',
   'A frightened teenage girl hiding in a classroom since the lights went out. She whispers, flinches at sudden movement, and trusts only slowly. She has watched the building in the dark and overheard things. She knows nothing about the world outside Sandby or why the stranger is here, and clams up if pushed on it.');

INSERT INTO story_nodes (id, arc_id, type, location_id, title, body, is_entry, is_death, gate_id, puzzle_id, media) VALUES
  ('kbk-classroom', 'main', 'gate', 'killebackskolan', 'The Classroom',
   'You follow a thin sound of crying into a darkened classroom. A girl is wedged between two desks, knees drawn to her chest. She freezes when she sees you, eyes wide. (Try to calm her enough to talk.)',
   FALSE, FALSE, 'kbk-marta-calm', NULL, '{"image_theme":"dark-classroom","music_theme":"fragile-quiet"}');

INSERT INTO story_edges (id, from_node, to_node, label, conditions, effects, danger, sort_order) VALUES
  ('e-hall-classroom', 'kbk-entrance-hall', 'kbk-classroom',
   'Follow the sound of crying into a classroom', '{"all":[]}',
   '{"progress_points":5,"log":"You found a frightened girl hiding in a classroom."}', 0, 4),
  ('e-classroom-leave', 'kbk-classroom', 'kbk-entrance-hall',
   'Step back into the hall', '{"all":[]}', '{"log":"You left the classroom."}', 0, 9);

INSERT INTO dialogue_gates (id, location_id, character_id, spec) VALUES
  ('kbk-marta-calm', 'killebackskolan', 'marta', '{
    "intent": "Calm the frightened girl enough that she will talk.",
    "criteria": [
      {"id":"reassured","desc":"Player is gentle, kind, or reassuring — tries to calm her, or promises not to hurt her."},
      {"id":"asked_something","desc":"Player asks her a question — who she is, what she saw, what happened, or how long she has been here."}
    ],
    "success_rule": "reassured AND asked_something",
    "knowledge_boundary": {
      "knows": ["she has been hiding in this classroom since the lights went out","she has seen the old caretaker creeping toward the boiler room in the dark"],
      "refuses": ["anything about why the stranger is here","the larger mystery"],
      "tone": "frightened teenage girl, whispers, flinches, trusts only slowly"
    },
    "hint_ladder": [
      "She presses herself into the corner and will not speak.",
      "She watches you, deciding whether to trust you.",
      "She whispers: \"I''ve been here since the lights died. I saw the caretaker... going down to the boiler room. In the dark.\""
    ],
    "mercy_after_attempts": 6,
    "on_success": {
      "progress_points": 35,
      "set_flag": "calmed_marta",
      "story_node_next": "kbk-classroom",
      "log": "You calmed Märta; she whispered what she had seen.",
      "write_memory": {"owner":"marta","content":"A kind stranger calmed me and I told them what I saw in the dark.","leakable":true}
    }
  }');
