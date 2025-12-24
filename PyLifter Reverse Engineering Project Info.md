Overall goal: Create a python program that can be used to manipulate up to 4 MyLifter winches over Bluetooth from the command line.

Feature list: Provide python implementations of the functions of the MyLifter Android app that includes (list is incomplete and may have additional features later):

* Connect to a new winch via Bluetooth pairing (requires pushing a pair button on the winch)  
* Group winches into a group that move together  
* Move winch (or group) up or down at max speed (either single winch or as a group)  
* Move winch (or group) up or down at variable speed (either single winch or as a group)  
* Move winch (or group) up until it reaches the high set point  
* Move winch (or group) down until it reaches the low set point  
* Display progress of movement  
* Set low stop point  
* Set high stop point  
* Markdown document of the MyLifter Bluetooth protocol and flows to control the MyLifter winch

Config: I expect an application config file might be useful for storing presets and other information necessary for the command line program to run successfully. For example:

* MAC Addresses of winches (1-4) to use for connecting and sending commands  
* Other configuration such as the high/low set points and group definitions

Reverse engineering resources: We do not have a Bluetooth specification for the commands and flows to operate the winch. But we do have:

* The decompiled MyLifter app APK source code located in the MyLifterApk-Source directory.  
* A working MyLifter Android app that we can use to execute flows and take a Wireshark Bluetooth packet capture that can be reviewed and compared to our python implementation. Example packet captures located in the BluetoothPacketCaptures directory. In the existing packet captures the bluetooth.addr \== cc:cc:cc:fe:15:33 is the first MyLifter test winch and bluetooth.addr \== b0:d5:fb:d1:f1:ed is the test Pixel 10 Pro XL used to run the Android app.  
* During the execution of the python harness and program you can use a shell command to take a Wireshark Bluetooth packet capture (e.g. tshark), store it and later analyze it with the python logs to determine if it was successful and if not, why it was not successful.

Implementation plan recommendations: 

* Document learnings during the reverse engineering phase. There will be important considerations with respect to timing and using parameters in Bluetooth responses in follow on Bluetooth commands.  
* Create utilities to capture and parse Wireshark .pcap .pcapng files during the reverse engineering loops to speed up progress.  
* The Bluetooth pairing for this device requires pushing a button on the winch. A human will have to do this manually. We can also consider creating a servo finger to do this to speed up the reverse engineering. I have access to a 3D printer and servos. Be creative and think about ways that you can automatically reverse engineer without the need for a human in the loop.  
* The winch has a single LED to show status. It can be green, yellow or red and flash at various intervals. If it would be helpful we can rig up a webcam and capture video or snapshots of the LED and winch movement that you can use to evaluate the status without the need for a human to facilitate the reverse engineering. Be creative and think about ways that you can automatically reverse engineer without the need for a human in the loop.  
* Take checkpoints by committing created code into my github project (https://github.com/sblu/PyLifter) to safely store progress.  
* Keep a [README.md](http://README.md) file up to date with instructions on usage of the generated code

Proposed phases: These are suggested ideas but you should expand and create a plan that makes the most sense.

1. Reverse engineer the MyLifter Bluetooth protocol  
   1. Review the source code and packet captures to create a simple harness that will connect and control a single winch. Request additional MyLifter Android app packet captures as needed.  
   2. Create a simple python harness to get a successful pairing and up and down movement of a single winch.  
   3. Extend simple python harness to get a successful pairing and up and down movement of two or more winches.  
2. Implement python library to simply control winches  
3. Implement robust command line Python program that can operate the winches from a Linux shell script via command line parameters (blocking until requested functions complete).  
4. Implement a robust interactive command line interface via a long running Python command line interface to control the winch.

Directories to be used for the project:

* BluetoothPacketCaptures: Bluetooth packet captures should be read from and written to this directory. Examples from the Android app are here to start with.  
* MyLifterApk-Source: This is the decompiled Android MyLifter app source code tree.  
* PyLifter: This is where source code associated with this project should be written. This is what should be committed to Github  
* [https://github.com/sblu/PyLifter](https://github.com/sblu/PyLifter): This is the Github repository for this project. It is initially blank except for a .gitignore file initially created for a Python project.

Additional Background: Here are some background resources for you to research to understand the MyLifter product.

* Overview of MyLifterApp: [https://www.youtube.com/watch?v=Yvhi7mbPxAk](https://www.youtube.com/watch?v=Yvhi7mbPxAk)  
* Link to the product page [https://www.smarterhome.com/collections/motorized-storage/products/mylifter-kits](https://www.smarterhome.com/collections/motorized-storage/products/mylifter-kits)  
* MyLifter manual: [https://static1.squarespace.com/static/58d1df9a46c3c49a233a9955/t/5c7ed60b24a6949b0b4ea773/1551816220062/mylifter\_new\_manual.pdf](https://static1.squarespace.com/static/58d1df9a46c3c49a233a9955/t/5c7ed60b24a6949b0b4ea773/1551816220062/mylifter_new_manual.pdf)