"""A provider error (a 502, a rate-limit) is not a real attempt.

The 57M night had a 502 mid-loop. An errored tick must not count toward the grind limit (it
proves nothing about whether the work is stuck), and it should briefly back the loop off so a
flapping upstream isn't hammered tick after tick.
"""
