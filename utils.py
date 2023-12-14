from typing import List, Tuple
import time


def error_to_strings(error: int) -> List[str]:
    if error == 0:
        return []
    else:
        errors = []
        if error & 0x01:
            errors.append('Door opened during heating')
        if error & 0x08:
            errors.append('Current temperature too low')
        if error & 0x10:
            errors.append('Current temperature too high')
        if error & 0x20:
            errors.append('Current temperature not rising during heating')
        if error & 0x40:
            errors.append('Fault while reading current temperature')
        if error & 0x80:
            errors.append('UI timeout')
        return errors


def calculate_derivative(data: List[Tuple[float, float]]) -> float:
    """
    Calculate the derivative of a list of data points tagged with times

    :param data: The list of data points
    :return: The derivative of the data points
    """
    if len(data) < 2:
        return 0

    # Calculate the differences and average them.
    diffs = []
    for i in range(1, len(data)):
        time_diff = data[i][0] - data[i - 1][0]
        temp_diff = data[i][1] - data[i - 1][1]
        diffs.append(temp_diff / time_diff)

    return sum(diffs) / len(diffs)