SET search_path = release_ops, public;

CREATE TABLE release_lane (
  lane_id text PRIMARY KEY,
  edge_scope text NOT NULL,
  opens_at time NOT NULL,
  closes_at time NOT NULL,
  CHECK(opens_at<closes_at)
);

CREATE TABLE release_window (
  window_id text PRIMARY KEY,
  lane_id text NOT NULL REFERENCES release_lane(lane_id),
  release_ref text NOT NULL,
  slot tstzrange NOT NULL,
  status text NOT NULL CHECK(status IN ('ACTIVE','CANCELLED')),
  source_pending_id text UNIQUE,
  CHECK(NOT isempty(slot)),
  CHECK(lower_inc(slot) AND NOT upper_inc(slot)),
  CHECK(lower(slot)<upper(slot))
);

CREATE TABLE pending_window (
  pending_id text PRIMARY KEY,
  lane_id text NOT NULL REFERENCES release_lane(lane_id),
  release_ref text NOT NULL,
  requested_slot tstzrange NOT NULL,
  priority integer NOT NULL CHECK(priority BETWEEN 0 AND 999),
  requested_at timestamptz NOT NULL,
  decision text NOT NULL DEFAULT 'PENDING' CHECK(decision IN ('PENDING','PROMOTED','LOST_PRIORITY','BLOCKED_OVERLAP')),
  decision_tx text,
  promoted_window_id text,
  CHECK(NOT isempty(requested_slot)),
  CHECK(lower_inc(requested_slot) AND NOT upper_inc(requested_slot)),
  CHECK(lower(requested_slot)<upper(requested_slot))
);

CREATE OR REPLACE FUNCTION release_ops.enforce_active_window_no_overlap()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS(
    SELECT 1 FROM release_window left_window
    JOIN release_window right_window
      ON left_window.lane_id=right_window.lane_id
     AND left_window.window_id<right_window.window_id
     AND left_window.status='ACTIVE'
     AND right_window.status='ACTIVE'
     AND left_window.slot && right_window.slot
  ) THEN
    RAISE EXCEPTION USING ERRCODE='23P01',MESSAGE='active release windows overlap at deferred commit';
  END IF;
  RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER release_window_active_no_overlap
AFTER INSERT OR UPDATE OF lane_id,slot,status ON release_window
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION release_ops.enforce_active_window_no_overlap();

CREATE OR REPLACE FUNCTION release_ops.swap_release_windows(p_left_window text,p_right_window text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE left_slot tstzrange; right_slot tstzrange; left_lane text; right_lane text;
BEGIN
  PERFORM 1 FROM release_window WHERE window_id IN(p_left_window,p_right_window) AND status='ACTIVE' ORDER BY window_id FOR UPDATE;
  SELECT slot,lane_id INTO STRICT left_slot,left_lane FROM release_window WHERE window_id=p_left_window AND status='ACTIVE';
  SELECT slot,lane_id INTO STRICT right_slot,right_lane FROM release_window WHERE window_id=p_right_window AND status='ACTIVE';
  IF left_lane<>right_lane THEN RAISE EXCEPTION 'release windows must belong to one lane'; END IF;
  UPDATE release_window SET slot=right_slot WHERE window_id=p_left_window;
  UPDATE release_window SET slot=left_slot WHERE window_id=p_right_window;
END;
$$;

CREATE OR REPLACE FUNCTION release_ops.move_release_window(p_window_id text,p_slot tstzrange)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE release_window SET slot=p_slot WHERE window_id=p_window_id AND status='ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'active release window not found: %',p_window_id; END IF;
END;
$$;

CREATE OR REPLACE FUNCTION release_ops.cancel_release_window(p_window_id text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE release_window SET status='CANCELLED' WHERE window_id=p_window_id AND status='ACTIVE';
  IF NOT FOUND THEN RAISE EXCEPTION 'active release window not found: %',p_window_id; END IF;
END;
$$;

CREATE OR REPLACE FUNCTION release_ops.decide_pending_window(p_lane_id text,p_slot tstzrange,p_tx_id text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE winner text; promoted_id text;
BEGIN
  PERFORM 1 FROM release_lane WHERE lane_id=p_lane_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'release lane not found: %',p_lane_id; END IF;
  IF EXISTS(SELECT 1 FROM release_window WHERE lane_id=p_lane_id AND status='ACTIVE' AND slot && p_slot) THEN
    UPDATE pending_window SET decision='BLOCKED_OVERLAP',decision_tx=p_tx_id
    WHERE lane_id=p_lane_id AND requested_slot=p_slot AND decision='PENDING';
    RETURN 'BLOCKED_OVERLAP';
  END IF;
  SELECT pending_id INTO winner FROM pending_window
  WHERE lane_id=p_lane_id AND requested_slot=p_slot AND decision='PENDING'
  ORDER BY priority DESC,requested_at ASC,pending_id ASC LIMIT 1 FOR UPDATE;
  IF winner IS NULL THEN RETURN 'NO_CANDIDATE'; END IF;
  promoted_id='PROM-'||winner;
  INSERT INTO release_window(window_id,lane_id,release_ref,slot,status,source_pending_id)
  SELECT promoted_id,lane_id,release_ref,requested_slot,'ACTIVE',pending_id FROM pending_window WHERE pending_id=winner;
  UPDATE pending_window SET decision=CASE WHEN pending_id=winner THEN 'PROMOTED' ELSE 'LOST_PRIORITY' END,
    decision_tx=p_tx_id,promoted_window_id=CASE WHEN pending_id=winner THEN promoted_id ELSE NULL END
  WHERE lane_id=p_lane_id AND requested_slot=p_slot AND decision='PENDING';
  RETURN winner;
END;
$$;

