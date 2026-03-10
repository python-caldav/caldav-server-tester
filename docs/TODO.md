* [x] Currently there is some differences in the logic dependent on if it can find a caldav test directory or not.  If it can find it, then it seems to disregard the ordinary servers in the caldav configuration.  I don't want this - the only thing I want is to have extra servers avaiable through the `--name` option if caldav test servers are found.
* [x] The default action now is "run towards all servers".  I think it should default to "don't run anything", with a `--all` flag that can be used if one wants this kind of behaviour.  Change of mind: we skip `--all` and the default is to connect to the default calendar server.
* [] It's needed to test it towards a server that does not allow calendars to be created
* [] Currently, by default it shows all "non-full features".  It should rather show all features deviating from the default.
* [] New TODO-items have been added to USAGE.md.  They should be fixed and removed from this file prior to the v1.0-release
* [] Search for other TODO-notes in the project
