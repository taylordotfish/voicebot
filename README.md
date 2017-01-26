voicebot
========

Version 0.1.1

**voicebot** automatically voices and devoices users in an IRC channel based
on activity. Users can see who's been active recently simply by looking at
which users are voiced.

See ``./voicebot.py --help`` for information on running voicebot.

Adding/removing users
---------------------

Users need to be in voicebot's lists of nicknames or NickServ accounts to be
voiced when active. There are multiple ways to add users:

* Send commands to voicebot through the command-line while it's running. Type
  "help" to see a list of commands.
* Send commands to voicebot through IRC. If you're a channel operator and
  present in the channel, you can send "\<nickname-of-voicebot\>: \<command\>"
  in the channel, or message voicebot directly with "\<command\>". Allowed
  commands are ``add-nickname``, ``add-account``, ``remove-nickname``, and
  ``remove-account``. The syntax is the same as it is through the command-line.
* Edit the files ``nicknames`` and ``accounts`` and restart voicebot (see the
  "Data files" section).

Data files
----------

voicebot stores data in a number of files (in the same directory as the
executable):

* ``nicknames`` contains a list of nicknames that will be managed. Users with
  these nicknames will be voiced when active.
* ``accounts`` contains a list of NickServ accounts that will be managed.
  Users logged into these accounts will be voiced when active.
* ``voicebot-data`` stores how recently users have been active.

What's new
----------

Version 0.1.1:

* Users are now voiced when joining if they have been active recently.

Dependencies
------------

* Python ≥ 3.5
* [pyrcb2](https://pypi.python.org/pypi/pyrcb2) ≥ 0.3.2
* [docopt](https://pypi.python.org/pypi/docopt) ≥ 0.6.6
