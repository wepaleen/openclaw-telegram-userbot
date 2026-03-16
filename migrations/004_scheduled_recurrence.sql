-- Add recurrence support to scheduled_actions (for recurring agent scenarios)
ALTER TABLE scheduled_actions ADD COLUMN recurrence TEXT;
