"""Central sample-size labels used across descriptive analytics."""


def sample_size_label(n: int) -> str:
    if n < 5:
        return "Très faible"
    if n < 10:
        return "Limité"
    if n < 25:
        return "Modéré"
    return "Solide descriptivement"
