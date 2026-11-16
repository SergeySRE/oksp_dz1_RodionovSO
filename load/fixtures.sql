-- WORKING seed uses a non-overlapping grid of slots for each specialist.
-- Pick separate ID lists before the run. Cancelling a booking cannot be
-- required for a slot in the free list to become available.
SELECT json_build_object(
    'slots', (
        SELECT array_agg(id ORDER BY id)
        FROM (
            SELECT s.id
            FROM sergey_rodionov.slots AS s
            WHERE NOT EXISTS (
                SELECT 1
                FROM sergey_rodionov.appointments AS a
                WHERE a.slot_id = s.id AND a.status = 'booked'
            )
            ORDER BY s.id LIMIT 10000
        ) AS free_slots
    ),
    'bookings', (
        SELECT array_agg(id ORDER BY id)
        FROM (
            SELECT id FROM sergey_rodionov.appointments
            WHERE status = 'booked'
            ORDER BY id LIMIT 10000
        ) AS active_bookings
    ),
    'occupied_slot', (
        SELECT slot_id FROM sergey_rodionov.appointments
        WHERE status = 'booked' ORDER BY id DESC LIMIT 1
    )
);
