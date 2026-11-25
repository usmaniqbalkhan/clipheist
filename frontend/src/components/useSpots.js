import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase.js';

// Early-access spots: TOTAL minus real sign-ups from the database. Refreshes every 30 s.
export const TOTAL = 50;
export default function useSpots() {
  const [spots, setSpots] = useState(null);
  useEffect(() => {
    if (!supabase) return undefined;
    let alive = true;
    const load = () => supabase.rpc('early_access_spots').then(({ data }) => { if (alive && data) setSpots({ total: TOTAL, taken: data.taken, left: Math.max(0, TOTAL - data.taken) }); });
    load();
    const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return spots;
}
