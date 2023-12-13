from typing import List


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
