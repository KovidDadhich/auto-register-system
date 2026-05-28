# Current Goal

Simplify the overall scheduling, queueing, catch-up, and retry architecture of the system.

---

# Current Problem

The current system is becoming overly complex because it attempts to process only selected Case IDs each day in order to reduce load on the court server.

Current logic includes:

* scheduled cases
* retry cases
* catch-up cases
* postponed/preponed hearings
* holiday handling
* delayed updates
* uncertain hearing changes

Since the project interacts with a government court system, there is significant unpredictability and randomness in hearing schedules and updates.

This creates several problems:

* complex queue management
* difficult edge-case handling
* fragile scheduling rules
* risk of accidentally missing cases
* difficult long-term maintenance

---

# New Planned Approach

Instead of processing only selected cases, the system will now process ALL Case IDs in the database every day.

To reduce server load:

* requests will be significantly delayed/throttled
* processing will be distributed across the entire day
* only a small number of requests will occur at a time

This removes the need for:

* complex queue prioritization
* catch-up logic
* retry scheduling complexity
* holiday-specific scheduling rules
* special handling for postponed/preponed dates

---

# Expected Benefits

* simpler architecture
* higher reliability
* lower risk of missed cases
* easier maintenance
* easier debugging
* easier future scaling
* more predictable workflow

---

# Current Task

Analyze the current architecture and identify:

1. which modules become unnecessary
2. which logic can be removed
3. how to simplify the scheduler/queue system
4. how to redesign workflow for full-database daily processing
5. safest migration strategy from current system
