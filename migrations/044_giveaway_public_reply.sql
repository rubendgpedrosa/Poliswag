-- Migration 044
-- Um só anúncio público do vencedor (cogs/giveaways.py): `announcement_reply_id`
-- é a resposta do bot ao anúncio; ao publicar um reroll o bot apaga-a e
-- responde de novo, por isso o canal nunca mostra que houve reroll.
-- `replacing_ids` (lista JSON) é quem um `!sorteio reroll` está a substituir
-- (já tinha o prémio, não apareceu); ao publicar sai de `winner_ids` e passa a
-- `excluded_ids`. Cancelar a revisão deixa tudo como estava.
ALTER TABLE giveaway
  ADD COLUMN IF NOT EXISTS announcement_reply_id BIGINT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS replacing_ids TEXT NULL;
