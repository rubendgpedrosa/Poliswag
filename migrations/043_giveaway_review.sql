-- Migration 043
-- Sorteios revistos pelos mods (cogs/giveaways.py): o bot já não anuncia
-- sozinho. Ao sortear, a linha fica `status = 'drawn'` com
-- `awaiting_review = 1` e quem saiu em `pending_ids`; a mensagem de revisão
-- no canal de moderação (`review_message_id`) tem os botões Publicar,
-- Repetir, Repetir sem este e Cancelar. `excluded_ids` são quem um mod tirou
-- do sorteio ("já tem o prémio"). `campfire_names` são os treinadores lidos
-- das capturas do Campfire (`!sorteio campfire`), que também entram.
-- pending_ids, excluded_ids e (desde 043) winner_ids guardam listas JSON:
-- um número é um utilizador do Discord, um texto um treinador do Campfire.
-- Linhas antigas ficam com awaiting_review = 0 (publicadas).
ALTER TABLE giveaway
  ADD COLUMN IF NOT EXISTS awaiting_review TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pending_ids TEXT NULL,
  ADD COLUMN IF NOT EXISTS excluded_ids TEXT NULL,
  ADD COLUMN IF NOT EXISTS review_message_id BIGINT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS campfire_names TEXT NULL;
