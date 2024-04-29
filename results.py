class SectionData:
    def __init__(self, timestamp, measurement, tags, fields):
        self.timestamp = timestamp
        self.measurement = measurement
        self.tags = tags
        self.fields = fields

    def __repr__(self):
        return f"SectionData(timestamp={self.timestamp}, measurement='{self.measurement}', tags={self.tags}, " \
               f"fields={self.fields})"


def get_sections(text):
    sections = {}
    for sections_text in text.split('--------------------------------------------------------------'):
        timestamp = None
        measurement = None
        tags = {}
        fields = {}
        for full_lines in sections_text.split('#'):
            if not full_lines:
                continue

            if full_lines.startswith(' TIMESTAMP:'):
                timestamp = int(full_lines.split(':')[1])
            elif full_lines.startswith(' MEASUREMENT:'):
                measurement = full_lines.split(' ')[3].strip()
            elif full_lines.startswith(' TAGS:'):
                for line in full_lines.split('\n')[1:]:
                    if not line:
                        continue
                    data = line.strip().split(' = ')
                    tags[data[0]] = data[1]
                pass
            elif full_lines.startswith(' FIELDS:'):
                for line in full_lines.split('\n')[1:]:
                    if not line:
                        continue
                    data = line.strip().split(' = ')
                    fields[data[0]] = data[1]

        if timestamp is not None and measurement is not None:
            sections[measurement] = SectionData(timestamp, measurement, tags, fields)

    return sections


def process_results(results_folder):

    # Read the results
    # make an array of results based on
    pass

# Example usage

def main():
    text = """\
    # TIMESTAMP: 638497082586960109
    # MEASUREMENT: [Application] Messages
    # TAGS:
                      mtype = counter
                       unit = items
    # FIELDS:
                      value = 4
    --------------------------------------------------------------
    # TIMESTAMP: 638497082586960109
    # MEASUREMENT: [Application] Succeeded
    # TAGS:
                      mtype = counter
                       unit = items
    # FIELDS:
                      value = 4
    --------------------------------------------------------------
    # TIMESTAMP: 638497082586960109
    # MEASUREMENT: [Application] engine_forkchoiceUpdatedV3
    # TAGS:
                      mtype = timer
                       unit = req
                   unit_dur = ms
                  unit_rate = ms
    # FIELDS:
                count.meter = 2
                     rate1m = 0
                     rate5m = 0
                    rate15m = 0
                  rate.mean = 0.005619814891839582
                    samples = 2
                       last = 8.879839
                 count.hist = 2
                        sum = 9.150521
                        min = 0.270682
                        max = 8.879839
                       mean = 4.5752605
                     median = 4.5752605
                     stddev = 6.087593294999634
                       p999 = 8.879839
                        p99 = 8.879839
                        p98 = 8.879839
                        p95 = 8.879839
                        p75 = 8.879839
    """

    sections = get_sections(text)

    for section in sections:
        print(section)
        print('---')


if __name__ == '__main__':
    main()
