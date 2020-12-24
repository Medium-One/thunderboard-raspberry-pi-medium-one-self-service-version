#!/usr/bin/env bash
sudo mkdir /m1
if [[ ! -f ./thundersense.sh ]] ; then
    echo 'ERROR: File "thundersense.sh" does not exist, aborting.'
    exit
fi
if [[ ! -f ./login.txt ]] ; then
    echo 'ERROR: File "login.txt" does not exist, aborting.'
    exit
fi
if [[ ! -f ./m1_thundersense_rpi_demo.py ]] ; then
    echo 'ERROR: File "m1_thundersense_rpi_demo.py" does not exist, aborting.'
    exit
fi
sudo mv ./thundersense.sh /etc/init.d/
sudo mv ./login.txt /m1/
sudo mv ./m1_thundersense_rpi_demo.py /m1/
sudo chmod 755 /etc/init.d/thundersense.sh
sudo chown root  /etc/init.d/thundersense.sh
sudo chgrp root  /etc/init.d/thundersense.sh
sudo update-rc.d thundersense.sh defaults
sudo apt-get update
sudo apt-get install python-requests
sudo apt-get install python-pip libglib2.0-dev
sudo pip install bluepy
