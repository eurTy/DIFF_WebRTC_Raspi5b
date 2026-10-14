import copy
import unittest
from capture_guard import eligible


class Guard(unittest.TestCase):
    def test_only_known_device_failure(self):
        info={'Name':'/video-processor','State':{'Status':'exited','ExitCode':1,'OOMKilled':False,'Error':'error gathering device information while adding custom device "/dev/video0": no such file or directory'},'HostConfig':{'RestartPolicy':{'Name':'always'},'Devices':[{'PathOnHost':'/dev/video0'}]}}
        self.assertTrue(eligible(info,True))
        self.assertFalse(eligible(info,False))
        for field,value in [('ExitCode',0),('Status','running'),('OOMKilled',True),('Error','segmentation fault')]:
            other=copy.deepcopy(info);other['State'][field]=value;self.assertFalse(eligible(other,True))
        other=copy.deepcopy(info);other['HostConfig']['RestartPolicy']['Name']='no';self.assertFalse(eligible(other,True))
        other=copy.deepcopy(info);other['Name']='/other';self.assertFalse(eligible(other,True))


if __name__=='__main__':unittest.main()
