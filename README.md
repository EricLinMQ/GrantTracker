# CorriLee Grant Ranker

CorriLee Grant Ranker is a desktop app that searches public grant websites and
puts the most relevant opportunities at the top of an Excel report. It is set
up for The CorriLee Foundation's regional documentary screenings and community
education work, with Australian opportunities and NSW sources prioritised.

You do not need Python, GitHub, ChatGPT, an API key, or a subscription to use
the downloaded app.

## Download the app

1. Open this repository's **Releases** page.
2. Open the newest release, shown at the top of the page.
3. Under **Assets**, download the ZIP for your computer:
   - **Windows x64** for most Windows computers.
   - **Linux x64** for most 64-bit Linux computers.
   - **Mac Apple Silicon** for Macs with an M1, M2, M3, M4, or newer M-series chip.
   - **Mac Intel** for older Intel-based Macs.
4. Extract the downloaded ZIP before opening the app.

If you are unsure which Mac you have, choose the Apple menu, select **About This
Mac**, and look for **Chip** or **Processor**.

## Open it for the first time

### Windows

Keep the complete extracted folder together, then open
**CorriLee Grant Ranker.exe**. The app does not need to be installed.

### Mac

Open **CorriLee Grant Ranker.app**. You may move it into your Applications
folder first.

### Linux

Open the extracted **CorriLee Grant Ranker** folder, then run the
**CorriLee Grant Ranker** file inside it. If your file manager asks what to do,
choose **Run** or **Execute**. The app does not need to be installed.

The current builds are not signed by a verified software publisher or notarised
by Apple, so Windows or macOS may show a security warning. Do not turn off your
computer's security features. If the computer will not let you open the app,
ask the person who manages your computer for help.

## Find and save grants

1. Make sure the computer is connected to the internet.
2. Open CorriLee Grant Ranker.
3. Select **Search grants**.
4. Keep the app open while it checks the listed websites. This can take several
   minutes.
5. Review the results in the app. Double-click a grant to open the funder's page.
6. Select **Save Excel** and choose where to save the report.
7. Select **Open saved file** to view the report in Excel or another spreadsheet
   program.

The app can create the report without Microsoft Excel, but you need Excel or
another spreadsheet viewer to read it.

If you select **Stop search**, the app finishes the web requests already under
way and lets you save the partial results. The report shows which sources could
not be read.

## Understand the score

Each grant receives a screening score out of 100 based on wording found on the
public grant page:

| What the app looks for | Points available |
| --- | ---: |
| Screenings, community education, awareness, discussion, or events | 35 |
| Child safety, sexual violence prevention, or community wellbeing | 30 |
| Regional, rural, or remote communities | 25 |
| Charities or not-for-profit applicants | 10 |

A higher score means the page contains more wording related to CorriLee's work.
It does not prove that CorriLee is eligible or likely to receive funding. A low
score can also mean that the app could not read all the guidelines.

A strong CorriLee activity or mission phrase—such as community education, awareness,
screenings, child safety, or sexual-violence prevention—is enough to enter the
shortlist. Broader opportunities are retained as **Needs review** when at least two
useful dimensions agree, such as community events plus regional focus. A single
generic match is omitted, as are titles that clearly concern drought, disaster,
infrastructure, construction, or another unrelated purpose. Omitted pages remain on
**Other pages checked** for transparency.

The Excel report includes:

- **Current shortlist**: current opportunities in ranked order.
- **Closed rounds**: opportunities whose listed closing date has passed.
- **Screening evidence**: the words that contributed to each score and anything
  that needs checking.
- **Source coverage**: websites checked, pages read, and access problems.
- **Other pages checked**: grant-like pages excluded for lacking a substantive
  connection to CorriLee's work.

Read the full funder guidelines before applying. Confirm the closing date,
eligible applicants, eligible activities, location rules, budget, and any other
requirements directly with the funder.

## What the app does and does not do

The app checks 13 configured public sources. It ranks readable grant pages using
fixed rules and supporting excerpts. It does not use AI to score grants.

It does not upload your documents, submit applications, guarantee that every
grant will be found, or confirm eligibility. Websites can change or block
automated reading, so an empty or incomplete report does not mean that no grants
are available.

## Help and updates

Download future versions from the **Releases** page in this repository. When
reporting a problem, include your operating system, the message shown by the app,
and the **Source coverage** worksheet from the saved report. Do not include
passwords, private application material, or personal information.

## Maintainer notes

The app uses Python 3.10 or newer and Tk. To run it from source, install
`requirements-build.txt`, run `python desktop_app.py`, and use
`python -m unittest discover -s . -v` for the offline regression suite.

GitHub Actions builds Windows x64, Linux x64, Mac Intel, and Mac Apple Silicon
packages, runs the tests and packaged-app smoke checks on each native runner, and
publishes the successful packages on the Releases page. The Windows and Mac
builds are currently unsigned, and the Mac builds are not notarised.
