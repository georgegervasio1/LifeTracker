"""Starter library for Goals: 100 well-known goals and challenges to browse and adopt.

Generic on purpose (this repo is public): no personal data, and no target numbers except where the
challenge itself defines one (6 marathon majors, 7 summits, 50 states, 26.2 miles...). Anything else the
user sets when adopting a goal, or works out with the coach.

Each entry: id, title, category, area, kind, unit, target, metric, steps.
  kind   "steps"  progress = share of plan steps done (a milestone plan)
         "number" a number the user updates (or a linked metric when `metric` is set)
  steps  suggested stages, in order, without dates; the coach or the user adds dates when adopting.
"""

AREAS = {"finance": "Finance", "health": "Health", "education": "Education", "personal": "Personal"}

CATEGORIES = ["Strength", "Endurance", "Sport and combat", "Adventure", "Travel", "Learning", "Money",
              "Career", "Mind and habits", "Creative", "People and giving"]

_AREA = {"Strength": "health", "Endurance": "health", "Sport and combat": "health", "Adventure": "personal",
         "Travel": "personal", "Learning": "education", "Money": "finance", "Career": "personal",
         "Mind and habits": "personal", "Creative": "personal", "People and giving": "personal"}

# (id, title, category, kind, unit, target, metric, steps)
_ITEMS = [
    # ---- Strength
    ("bodyweight-goal", "Reach a target bodyweight with muscle", "Strength", "number", "lb", None, None,
     ["Record a starting weight and measurements", "Set daily protein and calorie targets", "Follow a hypertrophy program for 12 weeks",
      "Re-weigh and adjust intake every 2 weeks", "Reassess and set the next block"]),
    ("bench-bodyweight", "Bench press your bodyweight", "Strength", "number", "lb", None, "health.lift_top:Bench Press", []),
    ("bench-315", "Bench press 315 lb", "Strength", "number", "lb", 315, "health.lift_top:Bench Press", []),
    ("squat-2x", "Squat twice your bodyweight", "Strength", "number", "lb", None, "health.lift_top:Squat", []),
    ("deadlift-500", "Deadlift 500 lb", "Strength", "number", "lb", 500, "health.lift_top:Deadlift", []),
    ("club-1000", "Join the 1,000 lb club (squat + bench + deadlift)", "Strength", "number", "lb", 1000, None, []),
    ("pullups-20", "Do 20 strict pull-ups", "Strength", "number", "reps", 20, None, []),
    ("pushups-100", "Do 100 push-ups in a row", "Strength", "number", "reps", 100, None, []),
    ("muscle-up", "Do a muscle-up", "Strength", "steps", None, None, None,
     ["Reach 12 strict pull-ups", "Learn chest-to-bar pull-ups", "Drill the transition on low rings or a band", "First muscle-up"]),
    ("handstand", "Hold a freestanding handstand for 30 seconds", "Strength", "number", "sec", 30, None, []),
    ("body-fat", "Reach a target body-fat percentage", "Strength", "number", "%", None, None, []),
    ("train-365", "Train 150 sessions in a year", "Strength", "number", "sessions", 150, "health.workouts_since_start", []),
    ("powerlifting-meet", "Compete in a powerlifting meet", "Strength", "steps", None, None, None,
     ["Pick a meet and register", "Learn the federation's commands and rules", "Run a 12-week meet prep", "Mock meet", "Compete"]),

    # ---- Endurance
    ("marathon", "Run a marathon", "Endurance", "steps", None, None, None,
     ["Build to 20 miles per week", "Register for a race", "Follow a 16–20 week plan", "Long run of 20 miles", "Taper and race"]),
    ("marathon-majors", "Run the six World Marathon Majors", "Endurance", "number", "races", 6, None,
     ["Tokyo", "Boston", "London", "Berlin", "Chicago", "New York"]),
    ("boston-qualify", "Qualify for the Boston Marathon", "Endurance", "steps", None, None, None, []),
    ("sub-4-marathon", "Run a sub-4-hour marathon", "Endurance", "steps", None, None, None, []),
    ("half-marathon", "Run a half marathon", "Endurance", "steps", None, None, None, []),
    ("ultra-50k", "Run a 50K ultramarathon", "Endurance", "steps", None, None, None, []),
    ("sub-20-5k", "Run a 5K under 20 minutes", "Endurance", "steps", None, None, None, []),
    ("mile-sub-6", "Run a mile under 6 minutes", "Endurance", "steps", None, None, None, []),
    ("run-1000-miles", "Run 1,000 miles in a year", "Endurance", "number", "mi", 1000, "health.run_miles_since_start", []),
    ("ironman", "Finish a full Ironman triathlon", "Endurance", "steps", None, None, None,
     ["Learn efficient freestyle; swim 2.4 miles continuously", "Get a road bike fitted; ride 100 miles", "Finish a sprint triathlon",
      "Finish an Olympic or 70.3", "Register for a full Ironman", "Follow a 24–30 week build", "Race"]),
    ("half-ironman", "Finish a 70.3 half Ironman", "Endurance", "steps", None, None, None, []),
    ("sprint-tri", "Finish a sprint triathlon", "Endurance", "steps", None, None, None, []),
    ("century-ride", "Ride a century (100 miles in a day)", "Endurance", "steps", None, None, None, []),
    ("swim-mile", "Swim a mile without stopping", "Endurance", "steps", None, None, None, []),
    ("open-water", "Finish an open-water swim race", "Endurance", "steps", None, None, None, []),
    ("hyrox", "Finish a HYROX race", "Endurance", "steps", None, None, None, []),
    ("spartan-trifecta", "Earn a Spartan Trifecta", "Endurance", "number", "races", 3, None, ["Sprint", "Super", "Beast"]),

    # ---- Sport and combat
    ("black-belt", "Earn a black belt", "Sport and combat", "steps", None, None, None,
     ["Choose a discipline and a school", "Train consistently (3 sessions a week)", "Test for each colored belt",
      "Compete or spar regularly", "Black belt test"]),
    ("boxing-match", "Fight in a sanctioned amateur boxing match", "Sport and combat", "steps", None, None, None,
     ["Join a boxing gym with a competition team", "Learn fundamentals for 3 months", "Start controlled sparring",
      "Get a physical and register with the sanctioning body", "Fight camp (8–10 weeks)", "First bout"]),
    ("dunk", "Dunk a basketball", "Sport and combat", "steps", None, None, None,
     ["Measure standing reach and vertical jump", "Touch the rim", "Grab the rim", "Dunk a volleyball or small ball", "Dunk a regulation ball"]),
    ("bjj-competition", "Compete in a jiu-jitsu tournament", "Sport and combat", "steps", None, None, None, []),
    ("golf-break-80", "Break 80 in golf", "Sport and combat", "steps", None, None, None, []),
    ("tennis-tournament", "Play in a tennis tournament", "Sport and combat", "steps", None, None, None, []),
    ("learn-surf", "Learn to surf", "Sport and combat", "steps", None, None, None, []),
    ("learn-ski-black", "Ski a black-diamond run", "Sport and combat", "steps", None, None, None, []),

    # ---- Adventure
    ("kilimanjaro", "Climb Mount Kilimanjaro", "Adventure", "steps", None, None, None,
     ["Pick a route (Lemosho or Machame for acclimatization) and an operator", "Get travel vaccines and insurance",
      "Hike with a weighted pack weekly", "Do a high-altitude practice hike", "Book flights and gear check", "Summit"]),
    ("seven-summits", "Climb the Seven Summits", "Adventure", "number", "summits", 7, None,
     ["Kilimanjaro", "Elbrus", "Aconcagua", "Denali", "Vinson", "Kosciuszko or Carstensz", "Everest"]),
    ("everest-base-camp", "Trek to Everest Base Camp", "Adventure", "steps", None, None, None, []),
    ("machu-picchu", "Hike the Inca Trail to Machu Picchu", "Adventure", "steps", None, None, None, []),
    ("rim-to-rim", "Hike the Grand Canyon rim to rim", "Adventure", "steps", None, None, None, []),
    ("fourteener", "Summit a Colorado 14er", "Adventure", "steps", None, None, None, []),
    ("skydive", "Go skydiving", "Adventure", "steps", None, None, None, []),
    ("scuba-cert", "Get scuba certified", "Adventure", "steps", None, None, None, []),
    ("pilot-license", "Earn a private pilot license", "Adventure", "steps", None, None, None, []),
    ("thru-hike", "Thru-hike a long trail section", "Adventure", "steps", None, None, None, []),
    ("national-parks", "Visit 20 U.S. national parks", "Adventure", "number", "parks", 20, None, []),
    ("northern-lights", "See the northern lights", "Adventure", "steps", None, None, None, []),
    ("safari", "Go on a safari", "Adventure", "steps", None, None, None, []),

    # ---- Travel
    ("50-states", "Visit all 50 states", "Travel", "number", "states", 50, None, []),
    ("7-continents", "Visit all seven continents", "Travel", "number", "continents", 7, None, []),
    ("30-countries", "Visit 30 countries", "Travel", "number", "countries", 30, None, []),
    ("wonders", "See the New Seven Wonders of the World", "Travel", "number", "wonders", 7, None, []),
    ("live-abroad", "Live abroad for a season", "Travel", "steps", None, None, None, []),
    ("solo-trip", "Take a solo trip abroad", "Travel", "steps", None, None, None, []),

    # ---- Learning
    ("calculus", "Relearn calculus", "Learning", "steps", None, None, None,
     ["Review algebra, functions and trig", "Limits and continuity", "Derivatives and their applications",
      "Integrals and the fundamental theorem", "Series", "Take a full practice exam"]),
    ("language-b2", "Reach conversational fluency (B2) in a language", "Learning", "steps", None, None, None, []),
    ("learn-code", "Build and ship a small software project", "Learning", "steps", None, None, None, []),
    ("read-52", "Read 52 books in a year", "Learning", "number", "books", 52, None, []),
    ("read-classics", "Read 25 classics", "Learning", "number", "books", 25, None, []),
    ("instrument", "Learn to play an instrument", "Learning", "steps", None, None, None, []),
    ("cfa-level-1", "Pass CFA Level I", "Learning", "steps", None, None, None, []),
    ("chess-1500", "Reach a 1500 chess rating", "Learning", "number", "rating", 1500, None, []),
    ("gpa-target", "Reach a target GPA", "Learning", "number", "GPA", None, "edu.gpa", []),
    ("finish-degree", "Finish a degree", "Learning", "number", "credits", None, "edu.credits", []),
    ("cook-50", "Learn to cook 50 dishes well", "Learning", "number", "dishes", 50, None, []),

    # ---- Money
    ("emergency-fund", "Build an emergency fund", "Money", "number", "$", None, "fin.cash", []),
    ("net-worth", "Reach a net worth milestone", "Money", "number", "$", None, "fin.net_worth", []),
    ("invest-milestone", "Reach an invested-assets milestone", "Money", "number", "$", None, "fin.invested", []),
    ("max-roth", "Max out a Roth IRA this year", "Money", "number", "$", None, None, []),
    ("debt-free", "Pay off all consumer debt", "Money", "number", "$", 0, None, []),
    ("budget-12", "Stay on budget for 12 months straight", "Money", "number", "months", 12, None, []),
    ("first-home", "Save a home down payment", "Money", "number", "$", None, None, []),
    ("side-income", "Earn first dollar of side income", "Money", "steps", None, None, None, []),

    # ---- Career
    ("bar-exam", "Pass the bar exam", "Career", "steps", None, None, None,
     ["Register for the exam and character and fitness", "Pick a bar prep course", "Finish the MPRE",
      "Complete the prep schedule", "Full-length practice exams", "Sit for the exam"]),
    ("public-speaking", "Give a talk to 100+ people", "Career", "steps", None, None, None, []),
    ("publish-article", "Publish an article", "Career", "steps", None, None, None, []),
    ("mentor", "Mentor someone for a year", "Career", "steps", None, None, None, []),
    ("network-50", "Have 50 coffee chats in a year", "Career", "number", "conversations", 50, None, []),
    ("start-business", "Start a business", "Career", "steps", None, None, None, []),

    # ---- Mind and habits
    ("meditate-100", "Meditate 100 days in a row", "Mind and habits", "number", "days", 100, None, []),
    ("sleep-8", "Average 8 hours of sleep for a month", "Mind and habits", "number", "hr", 8, "health.sleep_30d", []),
    ("recovery-avg", "Raise 30-day average recovery", "Mind and habits", "number", "%", None, "health.recovery_30d", []),
    ("no-alcohol-90", "Go 90 days without alcohol", "Mind and habits", "number", "days", 90, None, []),
    ("journal-365", "Journal every day for a year", "Mind and habits", "number", "days", 365, None, []),
    ("screen-time", "Cut daily phone screen time", "Mind and habits", "number", "hr/day", None, None, []),
    ("wake-early", "Wake up at the same early time for 30 days", "Mind and habits", "number", "days", 30, None, []),
    ("cold-plunge", "Take 30 cold plunges", "Mind and habits", "number", "sessions", 30, None, []),

    # ---- Creative
    ("write-book", "Write a book", "Creative", "steps", None, None, None, []),
    ("short-film", "Make a short film", "Creative", "steps", None, None, None, []),
    ("photo-project", "Finish a 365-day photo project", "Creative", "number", "photos", 365, None, []),
    ("learn-draw", "Learn to draw portraits", "Creative", "steps", None, None, None, []),
    ("perform-live", "Perform music live", "Creative", "steps", None, None, None, []),

    # ---- People and giving
    ("volunteer-100", "Volunteer 100 hours", "People and giving", "number", "hr", 100, None, []),
    ("charity-race", "Run a race for charity", "People and giving", "steps", None, None, None, []),
    ("give-blood", "Donate blood 4 times in a year", "People and giving", "number", "donations", 4, None, []),
    ("family-trip", "Plan a trip with family", "People and giving", "steps", None, None, None, []),
    ("pro-bono", "Take on a pro bono matter", "People and giving", "steps", None, None, None, []),
]


def _entry(t):
    gid, title, cat, kind, unit, target, metric, steps = t
    return {"id": gid, "title": title, "category": cat, "area": _AREA[cat], "kind": kind, "unit": unit,
            "target": target, "metric": metric, "steps": steps}


LIBRARY = [_entry(t) for t in _ITEMS]
BY_ID = {g["id"]: g for g in LIBRARY}
assert len(BY_ID) == len(LIBRARY), "duplicate library id"
