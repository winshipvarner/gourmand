#
# Regular cron jobs for the gourmand package.
#
0 4	* * *	root	[ -x /usr/bin/gourmand_maintenance ] && /usr/bin/gourmand_maintenance
