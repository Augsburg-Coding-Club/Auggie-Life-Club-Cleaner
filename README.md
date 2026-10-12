# Auggie-Life-Club-Cleaner

## The Problem

Removing old club members manually on Auggie Life is **really annoying**... 

For each name, you have to try to remember if they still go to the school anymore...
And if you can't remember, you have to manually check records and registration !
This can get **really annoying** if your club has *dozens* of members...

## The Solution 

This script checks each member of your club on [Auggie Life](https://augsburg.campuslabs.com/engage/), and if they don't have a match in [Augsburg Registration](registration.augsburg.edu), they get **removed** from the Auggie Life roster. We've tested this script manually, and it **doesn't remove anyone it shouldn't**. 

## Safety & Privacy

This is all done **above-board**. The script opens a chrome window, prompts you to log in to auget, and uses your credentials to access Auggie Life. So someone without remove permissions (*not* a club officer) **won't be able to kick everyone out of your club**. 

All of this is done **locally on your computer**, so *no* third party collecting your login info. Aside from Google, which would have done that anyway. 

## Setup

All you need is a computer with Python and Chrome, and to install the required packages. You can copy and paste this into your terminal:

```bash
pip install --upgrade playwright beautifulsoup4 requests
python -m playwright install chromium
```

And make sure to **edit the url** to reflect your club's roster page.

It's stored in the variable **ROSTER_BASE**

Right now, it's `https://augsburg.campuslabs.com/engage/actioncenter/organization/acc/roster`.


